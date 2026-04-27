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
        df['valor'] = df['valor'].astype(float) / 100 
        df = df.set_index('data')
        return df
    except Exception as e:
        st.error(f"Erro ao obter dados do Banco Central: {e}")
        return None

# Funções de Idade e Faixa Etária
def calcula_idade(data_nasc, data_ref):
    return data_ref.year - data_nasc.year - ((data_ref.month, data_ref.day) < (data_nasc.month, data_nasc.day))

def obter_faixa_etaria(idade):
    if idade <= 18: return 1
    elif idade <= 23: return 2
    elif idade <= 28: return 3
    elif idade <= 33: return 4
    elif idade <= 38: return 5
    elif idade <= 43: return 6
    elif idade <= 48: return 7
    elif idade <= 53: return 8
    elif idade <= 58: return 9
    else: return 10

def calcular_revisao(data_inicio, data_fim, data_nasc, valor_inicial, mes_reajuste, reajustes_plano_anual, reajustes_idade_cobrado, reajustes_idade_devido, df_fipe):
    meses_calculo = []
    
    data_atual = pd.to_datetime(data_inicio).replace(day=1)
    data_fim_calc = pd.to_datetime(data_fim)
    data_nasc_dt = pd.to_datetime(data_nasc)
    
    valor_devido = valor_inicial
    valor_cobrado = valor_inicial
    
    while data_atual <= data_fim_calc:
        ano_atual = data_atual.year
        mes_atual = data_atual.month
        
        perc_fipe_acumulado = 0.0
        perc_plano_anual = 0.0
        perc_idade_cobr = 0.0
        perc_idade_dev = 0.0
        mudou_faixa = "Não"
        
        idade_atual = calcula_idade(data_nasc_dt, data_atual)
        data_mes_anterior = data_atual - relativedelta(months=1)
        idade_anterior = calcula_idade(data_nasc_dt, data_mes_anterior)
        
        faixa_atual = obter_faixa_etaria(idade_atual)
        faixa_anterior = obter_faixa_etaria(idade_anterior)
        
        valor_anterior_cobrado = valor_cobrado
        valor_anterior_devido = valor_devido
        
        # 1. GATILHO DE FAIXA ETÁRIA (Aniversário e Mudança de Faixa)
        if faixa_atual > faixa_anterior:
            # Trava do Estatuto do Idoso: Sem reajuste se idade >= 60
            if idade_atual < 60:
                perc_idade_cobr = reajustes_idade_cobrado.get(faixa_atual, 0.0)
                perc_idade_dev = reajustes_idade_devido.get(faixa_atual, 0.0)
                mudou_faixa = f"Sim (Faixa {faixa_atual})"
            else:
                mudou_faixa = "Bloqueado (Idoso >=60)"

        # 2. GATILHO DE REAJUSTE ANUAL (Aniversário do Contrato)
        if mes_atual == mes_reajuste and data_atual > pd.to_datetime(data_inicio):
            fim_janela = data_atual - relativedelta(months=2)
            inicio_janela = data_atual - relativedelta(months=13)
            fim_janela = fim_janela.replace(day=1)
            inicio_janela = inicio_janela.replace(day=1)
            
            mask = (df_fipe.index >= inicio_janela) & (df_fipe.index <= fim_janela)
            dados_janela = df_fipe.loc[mask, 'valor']
            
            if len(dados_janela) > 0:
                fatores = 1 + dados_janela
                perc_fipe_acumulado = fatores.prod() - 1
                
            perc_plano_anual = reajustes_plano_anual.get(ano_atual, 0.0)
            
        # Aplica os reajustes (Anual + Idade)
        valor_devido = valor_devido * (1 + perc_fipe_acumulado) * (1 + perc_idade_dev)
        valor_cobrado = valor_cobrado * (1 + perc_plano_anual) * (1 + perc_idade_cobr)
        
        diferenca = valor_cobrado - valor_devido
        
        meses_calculo.append({
            'PERIODO_DT': data_atual,
            'PERIODO': data_atual.strftime('%d/%m/%Y'),
            'IDADE': idade_atual,
            'VALOR ANTERIOR COBRADO': valor_anterior_cobrado,
            'MUDOU FAIXA': mudou_faixa,
            '% FAIXA ETÁRIA (COBRADO)': perc_idade_cobr,
            '% FIPE SAUDE': perc_fipe_acumulado,
            '% DO PLANO ANUAL': perc_plano_anual,
            'VALOR DEVIDO': valor_devido,
            'VALOR COBRADO': valor_cobrado,
            'VALOR PAGO': valor_cobrado,
            'DIFERENÇA': diferenca
        })
        
        data_atual += relativedelta(months=1)
        
    return pd.DataFrame(meses_calculo)

# --- INTERFACE DO UTILIZADOR (FRONTEND) ---
st.title("⚖️ Sistema de Cálculos Revisionais - Planos de Saúde")
st.markdown("Plataforma SaaS para substituição do índice aplicado pelo **IPC-Fipe Saúde** com validação de Faixa Etária.")

df_fipe_global = obter_fipe_saude()

if df_fipe_global is not None:
    st.header("1. Dados do Processo e Beneficiário")
    col1, col2 = st.columns(2)
    with col1:
        parte_autora = st.text_input("Parte Autora")
        data_nascimento = st.date_input("Data de Nascimento do Titular", format="DD/MM/YYYY", value=datetime.date(1970, 1, 1))
        data_inicio = st.date_input("Data de Início do Cálculo", format="DD/MM/YYYY")
        mes_reajuste = st.number_input("Mês de Reajuste (Aniversário Contrato)", min_value=1, max_value=12, value=7)
    with col2:
        parte_re = st.text_input("Parte Ré (Ex: CASSI)")
        data_fim = st.date_input("Data Fim do Cálculo", format="DD/MM/YYYY")
        valor_inicial = st.number_input("Valor Inicial da Mensalidade (R$)", min_value=0.0, value=721.99, format="%.2f")
        data_base_prescricao = st.date_input("Data base para Prescrição (3 anos p/ trás)", format="DD/MM/YYYY", value=datetime.date.today())

    st.markdown("---")
    st.header("2. Reajustes por Faixa Etária (Evolução)")
    st.markdown("Insira os percentuais **cobrados pelo plano** e os **permitidos (devidos)**. O sistema fará a validação de compliance (Resolução 63/03).")
    
    reajustes_idade_cobrado = {}
    reajustes_idade_devido = {}
    
    faixas = [
        (2, "19 a 23 anos"), (3, "24 a 28 anos"), (4, "29 a 33 anos"),
        (5, "34 a 38 anos"), (6, "39 a 43 anos"), (7, "44 a 48 anos"),
        (8, "49 a 53 anos"), (9, "54 a 58 anos"), (10, "59 anos ou mais")
    ]
    
    # Criando colunas para a tabela de inserção de Idade
    col_f1, col_f2, col_f3 = st.columns(3)
    for idx, (faixa_id, label) in enumerate(faixas):
        col_atual = [col_f1, col_f2, col_f3][idx % 3]
        with col_atual:
            st.markdown(f"**{label}**")
            cobr = st.number_input(f"% Cobrado ({label})", min_value=0.0, value=0.0, format="%.2f", key=f"c_{faixa_id}")
            dev = st.number_input(f"% Legal/Devido ({label})", min_value=0.0, value=0.0, format="%.2f", key=f"d_{faixa_id}")
            if cobr > 0: reajustes_idade_cobrado[faixa_id] = cobr / 100
            if dev > 0: reajustes_idade_devido[faixa_id] = dev / 100
            st.markdown("")

    # MÓDULO DE COMPLIANCE (Validação das Travas ANS)
    st.subheader("🛡️ Análise de Legalidade (Travas da ANS)")
    preco_proj = {1: 1.0}
    for f in range(2, 11):
        preco_proj[f] = preco_proj[f-1] * (1 + reajustes_idade_cobrado.get(f, 0.0))
    
    regra_6x_ok = preco_proj[10] <= (preco_proj[1] * 6.0001)
    var_10_7 = preco_proj[10] / preco_proj[7] if 7 in preco_proj else 0
    var_7_1 = preco_proj[7] / preco_proj[1] if 7 in preco_proj else 0
    regra_acumulada_ok = var_10_7 <= (var_7_1 + 0.0001)

    if not regra_6x_ok or not regra_acumulada_ok:
        st.error("🚨 ATENÇÃO: Os percentuais cobrados pelo plano violam as regras da ANS!")
        if not regra_6x_ok:
            st.write("- **Falha na Trava de Amplitude:** A última faixa (59+) está superando em mais de 6x o valor da primeira faixa.")
        if not regra_acumulada_ok:
            st.write(f"- **Falha na Trava Acumulada:** A variação das faixas 7 a 10 ({var_10_7:.2f}x) é maior que a variação das faixas 1 a 7 ({var_7_1:.2f}x).")
    else:
        st.success("✅ Os percentuais de idade informados estão dentro das travas da ANS.")

    st.markdown("---")
    st.header("3. Reajustes Anuais (Aplicados pelo Plano)")
    
    reajustes_plano_anual = {}
    anos_range = range(data_inicio.year, data_fim.year + 1)
    cols = st.columns(min(len(anos_range), 4))
    for i, ano in enumerate(anos_range):
        with cols[i % 4]:
            val = st.number_input(f"Reajuste Anual - {ano} (%)", min_value=0.0, value=0.0, format="%.2f")
            if val > 0:
                reajustes_plano_anual[ano] = val / 100

    st.markdown("---")
    if st.button("Gerar Cálculo Revisional Completo", type="primary", use_container_width=True):
        with st.spinner('Processando Fipe Saúde, Idades e Travas...'):
            df_raw = calcular_revisao(
                data_inicio, data_fim, data_nascimento, valor_inicial, mes_reajuste, 
                reajustes_plano_anual, reajustes_idade_cobrado, reajustes_idade_devido, df_fipe_global
            )
            
            # --- CÁLCULO DA RESTITUIÇÃO (ÚLTIMOS 3 ANOS) ---
            limite_3_anos = pd.to_datetime(data_base_prescricao) - relativedelta(years=3)
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

            st.success("Cálculo gerado com sucesso!")
            st.subheader("📊 Resumo de Restituição")
            st.markdown(f"**Período Apurado (Prescrição Trienal):** {mes_inicio_resumo} a {mes_fim_resumo}")
            
            col_res1, col_res2, col_res3 = st.columns(3)
            col_res1.metric("Total Cobrado (3 anos)", f"R$ {soma_cobrado:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
            col_res2.metric("Total Devido (3 anos)", f"R$ {soma_devido:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
            col_res3.metric("🚨 Total a Restituir (Indébito)", f"R$ {soma_diferenca:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
            
            st.markdown("---")
            st.subheader("Detalhamento Mês a Mês")
            
            df_display = df_raw.copy()
            df_display.drop(columns=['PERIODO_DT'], inplace=True) 
            
            # Formatação de Percetuais
            df_display['% FIPE SAUDE'] = df_display['% FIPE SAUDE'].apply(lambda x: f"{x*100:,.2f}%".replace('.', ',') if x > 0 else "")
            df_display['% DO PLANO ANUAL'] = df_display['% DO PLANO ANUAL'].apply(lambda x: f"{x*100:,.2f}%".replace('.', ',') if x > 0 else "")
            df_display['% FAIXA ETÁRIA (COBRADO)'] = df_display['% FAIXA ETÁRIA (COBRADO)'].apply(lambda x: f"{x*100:,.2f}%".replace('.', ',') if x > 0 else "")
            
            # Formatação de Valores
            for col in ['VALOR ANTERIOR COBRADO', 'VALOR DEVIDO', 'VALOR COBRADO', 'VALOR PAGO', 'DIFERENÇA']:
                df_display[col] = df_display[col].apply(lambda x: f"{x:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))

            st.dataframe(df_display, use_container_width=True)
            
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
