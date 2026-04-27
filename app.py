import streamlit as st
import pandas as pd
import requests
from io import BytesIO
import datetime
from dateutil.relativedelta import relativedelta

# Configuração da página
st.set_page_config(page_title="SaaS - Revisão Plano de Saúde", layout="wide")

# Função para extrair dados do Banco Central (SGS - Fipe Saúde)
@st.cache_data(ttl=86400)
def obter_fipe_saude():
    url = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.7473/dados?formato=json"
    try:
        resposta = requests.get(url)
        resposta.raise_for_status()
        df = pd.DataFrame(resposta.json())
        df['data'] = pd.to_datetime(df['data'], format='%d/%m/%Y')
        df['valor'] = df['valor'].astype(float) / 100 # Converte para decimal (0.0052)
        df = df.set_index('data')
        return df
    except Exception as e:
        st.error(f"Erro ao obter dados do Banco Central: {e}")
        return None

def calcular_revisao(data_inicio, data_fim, valor_inicial, mes_reajuste, reajustes_plano, df_fipe):
    meses_calculo = []
    
    # Força os dias para o dia 1 do mês para facilitar a busca do índice
    data_atual = pd.to_datetime(data_inicio).replace(day=1)
    data_fim_calc = pd.to_datetime(data_fim)
    
    valor_devido = valor_inicial
    valor_cobrado = valor_inicial
    
    while data_atual <= data_fim_calc:
        ano_atual = data_atual.year
        mes_atual = data_atual.month
        
        perc_fipe_acumulado = 0.0
        perc_plano = 0.0
        
        # Só reajusta no mês de aniversário E se já passou do primeiro ano
        if mes_atual == mes_reajuste and data_atual > pd.to_datetime(data_inicio):
            # Calcula a Janela de 12 meses
            # Fim da janela: 2 meses antes do reajuste
            fim_janela = data_atual - relativedelta(months=2)
            # Início da janela: 13 meses antes do reajuste (para pegar 12 meses inteiros)
            inicio_janela = data_atual - relativedelta(months=13)
            
            fim_janela = fim_janela.replace(day=1)
            inicio_janela = inicio_janela.replace(day=1)
            
            # Filtra os índices nesse período
            mask = (df_fipe.index >= inicio_janela) & (df_fipe.index <= fim_janela)
            dados_janela = df_fipe.loc[mask, 'valor']
            
            if len(dados_janela) > 0:
                # Multiplicação de Fatores (Ex: 1.0052 * 1.0148...)
                fatores = 1 + dados_janela
                perc_fipe_acumulado = fatores.prod() - 1
            else:
                perc_fipe_acumulado = 0.0
                
            # Busca o reajuste do plano introduzido pelo usuário (já vem em decimal)
            perc_plano = reajustes_plano.get(ano_atual, 0.0)
            
            # Aplica os reajustes
            valor_devido = valor_devido * (1 + perc_fipe_acumulado)
            valor_cobrado = valor_cobrado * (1 + perc_plano)
            
        diferenca = valor_cobrado - valor_devido
        
        meses_calculo.append({
            'PERIODO_DT': data_atual, # Usado para cálculos internos e filtros
            'PERIODO': data_atual.strftime('%d/%m/%Y'), # Formato BR
            '% FIPE SAUDE': perc_fipe_acumulado,
            'VALOR DEVIDO': valor_devido,
            '% DO PLANO': perc_plano,
            'VALOR COBRADO': valor_cobrado,
            'VALOR PAGO': valor_cobrado,
            'DIFERENÇA': diferenca
        })
        
        data_atual += relativedelta(months=1)
        
    return pd.DataFrame(meses_calculo)

# --- INTERFACE DO UTILIZADOR (FRONTEND) ---
st.title("⚖️ Sistema de Cálculos Revisionais - Planos de Saúde")
st.markdown("Plataforma SaaS para substituição do índice aplicado pelo **IPC-Fipe Saúde**.")

df_fipe_global = obter_fipe_saude()

if df_fipe_global is not None:
    st.sidebar.success("✅ Dados Fipe Saúde do Banco Central sincronizados!")
    
    st.header("1. Dados do Processo")
    col1, col2 = st.columns(2)
    with col1:
        parte_autora = st.text_input("Parte Autora")
        data_inicio = st.date_input("Data de Início do Cálculo")
        mes_reajuste = st.number_input("Mês de Reajuste (Aniversário)", min_value=1, max_value=12, value=7)
    with col2:
        parte_re = st.text_input("Parte Ré (Ex: CASSI)")
        data_fim = st.date_input("Data Fim do Cálculo")
        valor_inicial = st.number_input("Valor Inicial da Mensalidade (R$)", min_value=0.0, value=721.99, format="%.2f")
        
    st.markdown("---")
    st.header("2. Prescrição Trienal")
    st.markdown("A restituição (indébito) considerará apenas os pagamentos a maior dos **últimos 3 anos** contados a partir da data abaixo (geralmente a data de distribuição da ação ou a data de hoje).")
    data_base_prescricao = st.date_input("Data para contagem de 3 anos para trás", value=datetime.date.today())

    st.markdown("---")
    st.header("3. Reajustes Aplicados pelo Plano")
    st.markdown("Digite os percentuais aplicados (Ex: Para **12,87%**, digite apenas **12.87**)")
    
    reajustes_plano = {}
    anos_range = range(data_inicio.year, data_fim.year + 1)
    
    # Cria inputs dinâmicos para os anos
    cols = st.columns(min(len(anos_range), 4))
    for i, ano in enumerate(anos_range):
        with cols[i % 4]:
            val = st.number_input(f"Reajuste Plano - {ano} (%)", min_value=0.0, value=0.0, format="%.2f")
            if val > 0:
                reajustes_plano[ano] = val / 100 # Divide por 100 para o cálculo matemático interno

    st.markdown("---")
    if st.button("Gerar Cálculo Revisional", type="primary", use_container_width=True):
        with st.spinner('Construindo a janela de 12 meses e cruzando dados do Banco Central...'):
            df_raw = calcular_revisao(
                data_inicio, data_fim, valor_inicial, mes_reajuste, reajustes_plano, df_fipe_global
            )
            
            # --- CÁLCULO DA RESTITUIÇÃO (ÚLTIMOS 3 ANOS) ---
            limite_3_anos = pd.to_datetime(data_base_prescricao) - relativedelta(years=3)
            # Filtra apenas as linhas onde a data do período é MAIOR ou IGUAL a exatos 3 anos atrás
            df_restituicao = df_raw[df_raw['PERIODO_DT'] >= limite_3_anos]
            
            if not df_restituicao.empty:
                soma_cobrado = df_restituicao['VALOR COBRADO'].sum()
                soma_devido = df_restituicao['VALOR DEVIDO'].sum()
                soma_diferenca = df_restituicao['DIFERENÇA'].sum()
                mes_inicio_resumo = df_restituicao['PERIODO_DT'].min().strftime('%m/%Y')
                mes_fim_resumo = df_restituicao['PERIODO_DT'].max().strftime('%m/%Y')
            else:
                soma_cobrado = soma_devido = soma_diferenca = 0
                mes_inicio_resumo = mes_fim_resumo = "N/A"

            # --- SEÇÃO: RESUMO DOS CÁLCULOS ---
            st.success("Cálculo gerado com sucesso!")
            st.subheader("📊 Resumo de Restituição")
            st.markdown(f"**Período Apurado (Prescrição Trienal):** {mes_inicio_resumo} a {mes_fim_resumo}")
            
            col_res1, col_res2, col_res3 = st.columns(3)
            col_res1.metric("Total Cobrado (3 anos)", f"R$ {soma_cobrado:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
            col_res2.metric("Total Devido (3 anos)", f"R$ {soma_devido:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
            col_res3.metric("🚨 Total a Restituir (Indébito)", f"R$ {soma_diferenca:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
            
            st.markdown("---")
            st.subheader("Detalhamento Mês a Mês")
            
            # Formata os dados APENAS para exibir bonito na tela e no Excel
            df_display = df_raw.copy()
            df_display.drop(columns=['PERIODO_DT'], inplace=True) # Esconde a coluna de data interna
            
            # Transforma os números em textos formatados como "12,87%" 
            df_display['% FIPE SAUDE'] = df_display['% FIPE SAUDE'].apply(lambda x: f"{x*100:,.2f}%".replace('.', ',') if x > 0 else "")
            df_display['% DO PLANO'] = df_display['% DO PLANO'].apply(lambda x: f"{x*100:,.2f}%".replace('.', ',') if x > 0 else "")
            
            # Arredonda e formata os valores em dinheiro
            for col in ['VALOR DEVIDO', 'VALOR COBRADO', 'VALOR PAGO', 'DIFERENÇA']:
                df_display[col] = df_display[col].apply(lambda x: f"{x:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))

            # Mostra a tabela na tela
            st.dataframe(df_display, use_container_width=True)
            
            # Prepara a exportação para Excel
            output = BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_display.to_excel(writer, index=False, sheet_name='Cálculo Revisional')
            conteudo_excel = output.getvalue()
            
            st.download_button(
                label="📥 Descarregar Planilha (Excel)",
                data=conteudo_excel,
                file_name=f"Calculo_Revisional_{parte_autora.replace(' ', '_')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
else:
    st.error("Falha ao comunicar com o Banco Central. Tente novamente mais tarde.")
