import streamlit as st
import pandas as pd
import requests
from io import BytesIO
import datetime
from dateutil.relativedelta import relativedelta

# Configuração da página
st.set_page_config(page_title="SaaS - Revisão Plano de Saúde", layout="wide")

# Função para extrair dados do Banco Central (SGS - Fipe Saúde)
@st.cache_data(ttl=86400) # Mantém em cache por 24h para não sobrecarregar o BCB
def obter_fipe_saude():
    url = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.7473/dados?formato=json"
    try:
        resposta = requests.get(url)
        resposta.raise_for_status()
        df = pd.DataFrame(resposta.json())
        df['data'] = pd.to_datetime(df['data'], format='%d/%m/%Y')
        df['valor'] = df['valor'].astype(float) / 100 # Converte para decimal
        df = df.set_index('data')
        return df
    except Exception as e:
        st.error(f"Erro ao obter dados do Banco Central: {e}")
        return None

def calcular_revisao(data_inicio, data_fim, valor_inicial, mes_reajuste, reajustes_plano, df_fipe):
    meses_calculo = []
    data_atual = pd.to_datetime(data_inicio)
    data_fim = pd.to_datetime(data_fim)
    
    valor_devido = valor_inicial
    valor_cobrado = valor_inicial
    
    while data_atual <= data_fim:
        ano_atual = data_atual.year
        mes_atual = data_atual.month
        
        perc_fipe = 0.0
        perc_plano = 0.0
        
        # Verifica se é o mês de reajuste (aniversário do contrato)
        if mes_atual == mes_reajuste and data_atual > pd.to_datetime(data_inicio):
            # Busca o FIPE Saúde correspondente (mês anterior ou o disponível)
            try:
                # Pegar o índice do ano/mês correspondente
                data_busca = pd.to_datetime(f"{ano_atual}-{mes_atual:02d}-01")
                if data_busca in df_fipe.index:
                    perc_fipe = df_fipe.loc[data_busca, 'valor']
                else:
                    # Pega o último disponível se o atual ainda não saiu
                    perc_fipe = df_fipe['valor'].iloc[-1] 
            except:
                perc_fipe = 0.0
                
            # Busca o reajuste do plano introduzido pelo utilizador
            perc_plano = reajustes_plano.get(ano_atual, 0.0)
            
            # Aplica os reajustes
            valor_devido = valor_devido * (1 + perc_fipe)
            valor_cobrado = valor_cobrado * (1 + perc_plano)
            
        diferenca = valor_cobrado - valor_devido
        
        meses_calculo.append({
            'PERIODO': data_atual.strftime('%Y-%m-%d'),
            '% FIPE SAUDE': perc_fipe if perc_fipe > 0 else '',
            'VALOR DEVIDO': round(valor_devido, 2),
            '% DO PLANO': perc_plano if perc_plano > 0 else '',
            'VALOR COBRADO': round(valor_cobrado, 2),
            'VALOR PAGO': round(valor_cobrado, 2), # Assumindo que o cliente pagou o que foi cobrado
            'DIFERENÇA': round(diferenca, 2)
        })
        
        data_atual += relativedelta(months=1)
        
    return pd.DataFrame(meses_calculo)

# --- INTERFACE DO UTILIZADOR (FRONTEND) ---

st.title("⚖️ Sistema de Cálculos Revisionais - Planos de Saúde")
st.markdown("Plataforma SaaS para substituição do índice aplicado pelo **IPC-Fipe Saúde**.")

df_fipe_global = obter_fipe_saude()

if df_fipe_global is not None:
    st.sidebar.success("✅ Dados do BCB (Fipe Saúde) atualizados!")
    
    st.header("1. Dados do Processo")
    col1, col2 = st.columns(2)
    with col1:
        parte_autora = st.text_input("Parte Autora")
        data_inicio = st.date_input("Data de Início do Cálculo")
        mes_reajuste = st.number_input("Mês de Reajuste (Aniversário)", min_value=1, max_value=12, value=7)
    with col2:
        parte_re = st.text_input("Parte Ré (Ex: CASSI)")
        data_fim = st.date_input("Data Fim do Cálculo")
        valor_inicial = st.number_input("Valor Inicial da Mensalidade (€/R$)", min_value=0.0, value=721.99, format="%.2f")

    st.header("2. Reajustes Aplicados pelo Plano")
    st.markdown("Introduza os percentuais cobrados pela operadora em cada ano (em formato decimal. Ex: 0.1287 para 12,87%)")
    
    reajustes_plano = {}
    anos_range = range(data_inicio.year, data_fim.year + 1)
    
    # Cria inputs dinâmicos para os anos
    cols = st.columns(min(len(anos_range), 4))
    for i, ano in enumerate(anos_range):
        with cols[i % 4]:
            val = st.number_input(f"Reajuste Plano - {ano}", min_value=0.0, value=0.0, format="%.4f")
            if val > 0:
                reajustes_plano[ano] = val

    if st.button("Gerar Cálculo Revisional", type="primary"):
        with st.spinner('A processar cálculos e a cruzar dados do Banco Central...'):
            df_resultado = calcular_revisao(
                data_inicio, data_fim, valor_inicial, mes_reajuste, reajustes_plano, df_fipe_global
            )
            
            st.success("Cálculo gerado com sucesso!")
            
            # Mostra a tabela na ecrã
            st.dataframe(df_resultado, use_container_width=True)
            
            # Cálculo de totais
            total_indebito = df_resultado['DIFERENÇA'].sum()
            st.metric(label="Total da Diferença a Restituir (Indébito)", value=f"{total_indebito:,.2f}")
            
            # Prepara a exportação para Excel
            output = BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_resultado.to_excel(writer, index=False, sheet_name='Cálculo Revisional')
            conteudo_excel = output.getvalue()
            
            st.download_button(
                label="📥 Descarregar Planilha (Excel)",
                data=conteudo_excel,
                file_name=f"Calculo_Revisional_{parte_autora.replace(' ', '_')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
else:
    st.error("Falha ao comunicar com o Banco Central. Tente novamente mais tarde.")
