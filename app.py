import streamlit as st
import pandas as pd
import requests
from io import BytesIO
import datetime
from dateutil.relativedelta import relativedelta

st.set_page_config(page_title="SaaS - Revisão Plano de Saúde", layout="wide")

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

def calcular_revisao_automatica(data_inicio, data_fim, data_nasc, valor_inicial, mes_reajuste, dict_valores_informados, reajustes_idade_devido, df_fipe):
    meses_calculo = []
    
    data_atual = pd.to_datetime(data_inicio).replace(day=1)
    data_fim_calc = pd.to_datetime(data_fim).replace(day=1) # Normaliza para o dia 1
    data_nasc_dt = pd.to_datetime(data_nasc)
    
    valor_devido = valor_inicial
    valor_cobrado = valor_inicial
    
    # Dicionário para guardar os reajustes de idade que o sistema descobrir (Para testar na ANS depois)
    reajustes_idade_descobertos = {}
    
    while data_atual <= data_fim_calc:
        ano_atual = data_atual.year
        mes_atual = data_atual.month
        chave_mes_ano = data_atual.strftime('%m/%Y')
        
        perc_fipe_acumulado = 0.0
        perc_idade_dev = 0.0
        perc_cobrado_real = 0.0
        motivo_reajuste = "-"
        
        idade_atual = calcula_idade(data_nasc_dt, data_atual)
        data_mes_anterior = data_atual - relativedelta(months=1)
        idade_anterior = calcula_idade(data_nasc_dt, data_mes_anterior)
        
        faixa_atual = obter_faixa_etaria(idade_atual)
        faixa_anterior = obter_faixa_etaria(idade_anterior)
        
        valor_anterior_cobrado = valor_cobrado
        
        # --- 1. LÓGICA DO VALOR COBRADO (Leitura Automática) ---
        # Se o usuário informou um novo valor na tabela para este mês
        if chave_mes_ano in dict_valores_informados:
            novo_valor = dict_valores_informados[chave_mes_ano]
            if novo_valor > 0 and novo_valor != valor_cobrado:
                perc_cobrado_real = (novo_valor / valor_cobrado) - 1
                valor_cobrado = novo_valor
                
                # O sistema adivinha o motivo da cobrança
                if mes_atual == mes_reajuste and faixa_atual > faixa_anterior:
                    motivo_reajuste = f"Misto (Anual + Faixa {faixa_atual})"
                    reajustes_idade_descobertos[faixa_atual] = perc_cobrado_real # Guarda para análise
                elif mes_atual == mes_reajuste:
                    motivo_reajuste = "Reajuste Anual"
                elif faixa_atual > faixa_anterior:
                    motivo_reajuste = f"Mudança de Faixa ({faixa_atual})"
                    reajustes_idade_descobertos[faixa_atual] = perc_cobrado_real # Guarda para análise
                else:
                    motivo_reajuste = "Aumento Avulso"
        else:
            # Continua cobrando o mesmo valor do mês anterior
            perc_cobrado_real = 0.0
            
            # Mesmo que o valor não tenha subido, precisamos checar se o plano deixou de cobrar a faixa
            if faixa_atual > faixa_anterior:
                motivo_reajuste = f"Mudou Faixa ({faixa_atual}) - Sem aumento"

        # --- 2. LÓGICA DO VALOR DEVIDO (O Correto a Pagar) ---
        # Gatilho de Faixa Etária Legal
        if faixa_atual > faixa_anterior:
            if idade_atual < 60:
                perc_idade_dev = reajustes_idade_devido.get(faixa_atual, 0.0)
                valor_devido *= (1 + perc_idade_dev)

        # Gatilho de Reajuste Anual Legal (FIPE Saúde)
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
                valor_devido *= (1 + perc_fipe_acumulado)
        
        diferenca = valor_cobrado - valor_devido
        
        meses_calculo.append({
            'PERIODO_DT': data_atual,
            'PERIODO': data_atual.strftime('%d/%m/%Y'),
            'IDADE': idade_atual,
            'VALOR ANT. COBRADO': valor_anterior_cobrado,
            'MOTIVO AUMENTO PLANO': motivo_reajuste,
            '% APLICADO PLANO': perc_cobrado_real,
            '% FIPE (LEGAL)': perc_fipe_acumulado,
            '% FAIXA (LEGAL)': perc_idade_dev,
            'VALOR DEVIDO': valor_devido,
            'VALOR COBRADO': valor_cobrado,
            'DIFERENÇA': diferenca
        })
        
        data_atual += relativedelta(months=1)
        
    return pd.DataFrame(meses_calculo), reajustes_idade_descobertos

# --- INTERFACE DO UTILIZADOR (FRONTEND) ---
st.title("⚖️ Sistema Revisional Inteligente - Planos de Saúde")
st.markdown("O sistema calcula automaticamente os percentuais embutidos nas mensalidades e testa a legalidade contra o IPC-Fipe Saúde e as regras da ANS.")

df_fipe_global = obter_fipe_saude()

if df_fipe_global is not None:
    st.header("1. Dados do Processo")
    col1, col2 = st.columns(2)
    with col1:
        parte_autora = st.text_input("Parte Autora")
        data_nascimento = st.date_input("Data de Nascimento do Titular", format="DD/MM/YYYY", value=datetime.date(1970, 1, 1))
        data_inicio = st.date_input("Data de Início do Cálculo", format="DD/MM/YYYY")
        mes_reajuste = st.number_input("Mês de Reajuste (Aniversário Contrato)", min_value=1, max_value=12, value=7)
    with col2:
        parte_re = st.text_input("Parte Ré (Ex: CASSI)")
        data_fim = st.date_input("Data Fim do Cálculo", format="DD/MM/YYYY")
        valor_inicial = st.number_input("Valor Inicial (Primeiro Mês R$)", min_value=0.0, value=721.99, format="%.2f")
        data_base_prescricao = st.date_input("Data para Prescrição (3 anos p/ trás)", format="DD/MM/YYYY", value=datetime.date.today())

    st.markdown("---")
    st.header("2. Evolução dos Boletos (O que o Plano Cobrou)")
    st.markdown("Em vez de adivinhar porcentagens, **adicione linhas na tabela abaixo** sempre que a mensalidade aumentou. O sistema fará a engenharia reversa.")
    
    # Cria um DataFrame inicial para a tabela dinâmica
    df_valores_iniciais = pd.DataFrame({
        "Mês/Ano (MM/AAAA)": [(data_inicio + relativedelta(years=1)).strftime('%m/%Y')],
        "Valor Cobrado (R$)": [850.00]
    })
    
    # Componente de Tabela Editável (O usuário pode adicionar quantas linhas quiser)
    df_valores_editado = st.data_editor(
        df_valores_iniciais, 
        num_rows="dynamic", 
        use_container_width=True,
        column_config={
            "Valor Cobrado (R$)": st.column_config.NumberColumn("Valor Cobrado (R$)", format="R$ %.2f", min_value=0.0)
        }
    )

    st.markdown("---")
    st.header("3. Parâmetros Legais (O que é Devido)")
    st.markdown("Insira qual é o percentual **PERMITIDO POR LEI/CONTRATO** para cada faixa etária.")
    
    reajustes_idade_devido = {}
    faixas = [
        (2, "19 a 23 anos"), (3, "24 a 28 anos"), (4, "29 a 33 anos"),
        (5, "34 a 38 anos"), (6, "39 a 43 anos"), (7, "44 a 48 anos"),
        (8, "49 a 53 anos"), (9, "54 a 58 anos"), (10, "59 anos ou mais")
    ]
    
    cols_f = st.columns(5)
    for idx, (faixa_id, label) in enumerate(faixas):
        with cols_f[idx % 5]:
            dev = st.number_input(f"Legal: {label} (%)", min_value=0.0, value=0.0, format="%.2f", key=f"d_{faixa_id}")
            if dev > 0: reajustes_idade_devido[faixa_id] = dev / 100

    st.markdown("---")
    if st.button("Gerar Cálculo Revisional Completo", type="primary", use_container_width=True):
        with st.spinner('Analisando histórico de boletos, calculando percentuais e cruzando com o BCB...'):
            
            # Converte a tabela digitada pelo usuário num Dicionário Python { '07/2018': 850.00 }
            dict_valores = {}
            for index, row in df_valores_editado.iterrows():
                try:
                    mes_ano = str(row["Mês/Ano (MM/AAAA)"]).strip()
                    val = float(row["Valor Cobrado (R$)"])
                    if mes_ano != "nan" and val > 0:
                        dict_valores[mes_ano] = val
                except:
                    pass

            # Roda o super motor de cálculo
            df_raw, idades_descobertas = calcular_revisao_automatica(
                data_inicio, data_fim, data_nascimento, valor_inicial, mes_reajuste, 
                dict_valores, reajustes_idade_devido, df_fipe_global
            )
            
            # --- MÓDULO DE COMPLIANCE (Validação das Travas ANS pós-cálculo) ---
            st.subheader("🛡️ Análise de Legalidade (Engenharia Reversa ANS)")
            if idades_descobertas:
                preco_proj = {1: 1.0}
                for f in range(2, 11):
                    # Pega o percentual que o sistema descobriu. Se não descobriu (ainda não ocorreu), usa 0
                    preco_proj[f] = preco_proj[f-1] * (1 + idades_descobertas.get(f, 0.0))
                
                regra_6x_ok = preco_proj[10] <= (preco_proj[1] * 6.0001)
                var_10_7 = preco_proj[10] / preco_proj[7] if 7 in preco_proj else 0
                var_7_1 = preco_proj[7] / preco_proj[1] if 7 in preco_proj else 0
                regra_acumulada_ok = var_10_7 <= (var_7_1 + 0.0001)

                if not regra_6x_ok or not regra_acumulada_ok:
                    st.error("🚨 ALERTA DE ABUSIVIDADE: O sistema identificou que a operadora quebrou as regras da ANS!")
                    if not regra_6x_ok:
                        st.write("- **FALHA (Amplitude):** A evolução projetada da última faixa atinge um valor mais de 6x superior à primeira.")
                    if not regra_acumulada_ok:
                        st.write(f"- **FALHA (Acumulada):** A variação imposta nas faixas 7 a 10 ({var_10_7:.2f}x) foi maior que nas faixas 1 a 7 ({var_7_1:.2f}x).")
                else:
                    st.success("✅ Os reajustes de faixa etária cobrados e identificados no período estão dentro das travas matemáticas da ANS.")
            else:
                st.info("ℹ️ Nenhuma mudança de faixa etária foi identificada no período lançado.")

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

            st.markdown("---")
            st.subheader("📊 Resumo de Restituição Financeira")
            st.markdown(f"**Período Apurado (Prescrição Trienal):** {mes_inicio_resumo} a {mes_fim_resumo}")
            
            col_res1, col_res2, col_res3 = st.columns(3)
            col_res1.metric("Total Cobrado (3 anos)", f"R$ {soma_cobrado:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
            col_res2.metric("Total Devido (3 anos)", f"R$ {soma_devido:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
            col_res3.metric("🚨 Indébito Simples (Sem Juros)", f"R$ {soma_diferenca:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
            
            st.markdown("---")
            st.subheader("Detalhamento Mês a Mês")
            
            df_display = df_raw.copy()
            df_display.drop(columns=['PERIODO_DT'], inplace=True) 
            
            # Formatação visual de porcentagens
            cols_perc = ['% APLICADO PLANO', '% FIPE (LEGAL)', '% FAIXA (LEGAL)']
            for col in cols_perc:
                df_display[col] = df_display[col].apply(lambda x: f"{x*100:,.2f}%".replace('.', ',') if x > 0 else "-")
            
            # Formatação de Valores Monetários
            cols_dinheiro = ['VALOR ANT. COBRADO', 'VALOR DEVIDO', 'VALOR COBRADO', 'DIFERENÇA']
            for col in cols_dinheiro:
                df_display[col] = df_display[col].apply(lambda x: f"R$ {x:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))

            st.dataframe(df_display, use_container_width=True)
            
            output = BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_display.to_excel(writer, index=False, sheet_name='Cálculo Revisional')
                # Dá um toque estético na planilha Excel
                worksheet = writer.sheets['Cálculo Revisional']
                worksheet.set_column('A:B', 12)
                worksheet.set_column('C:K', 18)

            conteudo_excel = output.getvalue()
            
            st.download_button(
                label="📥 Descarregar Planilha Completa (Excel)",
                data=conteudo_excel,
                file_name=f"Calculo_Inteligente_{parte_autora.replace(' ', '_')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
else:
    st.error("Falha ao comunicar com o Banco Central. Tente novamente mais tarde.")
