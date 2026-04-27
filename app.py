import streamlit as st
import pandas as pd
import requests
from io import BytesIO
import datetime
from dateutil.relativedelta import relativedelta
import google.generativeai as genai
import json
import tempfile
import os

st.set_page_config(page_title="HSA Advogados - Revisão de Saúde", layout="wide")

# --- DICIONÁRIOS DE TRADUÇÃO (Blindagem contra meses em inglês) ---
MESES_ABREV = {1: 'jan', 2: 'fev', 3: 'mar', 4: 'abr', 5: 'mai', 6: 'jun', 7: 'jul', 8: 'ago', 9: 'set', 10: 'out', 11: 'nov', 12: 'dez'}
MESES_COMPLETO = {1: 'JANEIRO', 2: 'FEVEREIRO', 3: 'MARÇO', 4: 'ABRIL', 5: 'MAIO', 6: 'JUNHO', 7: 'JULHO', 8: 'AGOSTO', 9: 'SETEMBRO', 10: 'OUTUBRO', 11: 'NOVEMBRO', 12: 'DEZEMBRO'}

# --- MEMÓRIA DO SISTEMA ---
if 'parte_autora' not in st.session_state: st.session_state.parte_autora = ""
if 'valor_inicial' not in st.session_state: st.session_state.valor_inicial = 0.0
if 'data_nascimento' not in st.session_state: st.session_state.data_nascimento = datetime.date(1970, 1, 1)
if 'data_inicio' not in st.session_state: st.session_state.data_inicio = datetime.date(2015, 1, 1)
if 'df_valores_iniciais' not in st.session_state: 
    st.session_state.df_valores_iniciais = pd.DataFrame({"Mês/Ano (MM/AAAA)": [""], "Valor Cobrado (R$)": [0.0]})

for f_id in range(2, 11):
    if f'd_{f_id}' not in st.session_state:
        st.session_state[f'd_{f_id}'] = 0.0

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
        st.error(f"Erro ao obter dados do BCB: {e}")
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
    data_fim_calc = pd.to_datetime(data_fim).replace(day=1)
    data_nasc_dt = pd.to_datetime(data_nasc)
    
    valor_devido = valor_inicial
    valor_cobrado = valor_inicial
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
        
        # 1. Valor Cobrado (Lógica linha a linha)
        if chave_mes_ano in dict_valores_informados:
            novo_valor = dict_valores_informados[chave_mes_ano]
            if novo_valor > 0 and abs(novo_valor - valor_cobrado) > 0.05: 
                perc_cobrado_real = (novo_valor / valor_cobrado) - 1
                valor_cobrado = novo_valor
                
                if mes_atual == mes_reajuste and faixa_atual > faixa_anterior:
                    motivo_reajuste = f"Misto (Anual + Faixa {faixa_atual})"
                    reajustes_idade_descobertos[faixa_atual] = perc_cobrado_real 
                elif mes_atual == mes_reajuste:
                    motivo_reajuste = "Reajuste Anual"
                elif faixa_atual > faixa_anterior:
                    motivo_reajuste = f"Mudança de Faixa ({faixa_atual})"
                    reajustes_idade_descobertos[faixa_atual] = perc_cobrado_real 
                else:
                    motivo_reajuste = "Aumento Avulso"
        else:
            perc_cobrado_real = 0.0
            if faixa_atual > faixa_anterior:
                motivo_reajuste = f"Mudou Faixa ({faixa_atual}) - Sem aumento"

        # 2. Valor Devido (Lógica linha a linha - Mensalidade Anterior * (1 + % FIPE))
        if faixa_atual > faixa_anterior and idade_atual < 60:
            perc_idade_dev = reajustes_idade_devido.get(faixa_atual, 0.0)
            valor_devido *= (1 + perc_idade_dev)

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
        
        # 3. Diferença
        diferenca = valor_cobrado - valor_devido
        
        periodo_str = f"{MESES_ABREV[data_atual.month]}/{data_atual.strftime('%y')}"
        
        meses_calculo.append({
            'PERIODO_DT': data_atual,
            'PERIODO [1]': periodo_str,
            '% FIPE SAUDE [2]': perc_fipe_acumulado,
            'VALOR DEVIDO [3]': valor_devido,
            '% DO PLANO [4]': perc_cobrado_real,
            'VALOR COBRADO [5]': valor_cobrado,
            'SUSPENÇÃO DE REAJUSTE [6]': "",
            'VALOR PAGO [7]': valor_cobrado,
            'DIFERENÇA [8]': diferenca,
            'OBSERVAÇÃO': motivo_reajuste
        })
        
        data_atual += relativedelta(months=1)
        
    return pd.DataFrame(meses_calculo), reajustes_idade_descobertos

# --- MENU LATERAL: IA ---
with st.sidebar:
    st.header("🤖 Assistente de IA")
    api_key = st.text_input("Sua Chave API do Gemini", type="password")
    texto_ia = st.text_area("Instruções em texto:", height=100)
    arquivos_enviados = st.file_uploader("Documentos", accept_multiple_files=True, type=['pdf', 'png', 'jpg'])
    
    if st.button("Processar Dados com IA", type="primary", use_container_width=True):
        if not api_key: st.error("Insira a chave da API.")
        else:
            with st.spinner("Processando..."):
                try:
                    genai.configure(api_key=api_key)
                    modelos = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                    nome_modelo = next((m for termo in ["gemini-1.5-flash", "gemini-2", "gemini-flash"] for m in modelos if termo in m), modelos[0])
                    modelo = genai.GenerativeModel(nome_modelo.replace("models/", ""))
                    
                    conteudo = [f"Extraia os dados. Instruções: {texto_ia}\nDevolva JSON: {{\"parte_autora\": \"\", \"data_nascimento\": \"DD/MM/AAAA\", \"data_inicio\": \"DD/MM/AAAA\", \"valor_primeiro_boleto\": 100.0, \"boletos\": [{{\"mes_ano\": \"MM/AAAA\", \"valor\": 150.0}}]}}"]
                    arquivos_temp = []
                    for arq in arquivos_enviados:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{arq.name.split('.')[-1]}") as t:
                            t.write(arq.read())
                            t_path = t.name
                        subido = genai.upload_file(t_path)
                        conteudo.append(subido)
                        arquivos_temp.append(subido)
                        os.unlink(t_path)
                    
                    resp = modelo.generate_content(conteudo)
                    for a in arquivos_temp: genai.delete_file(a.name)
                    
                    dados = json.loads(resp.text.replace("```json", "").replace("```", "").strip())
                    if dados.get('parte_autora'): st.session_state.parte_autora = dados['parte_autora']
                    if dados.get('valor_primeiro_boleto'): st.session_state.valor_inicial = float(dados['valor_primeiro_boleto'])
                    try:
                        if dados.get('data_nascimento'): st.session_state.data_nascimento = datetime.datetime.strptime(dados['data_nascimento'], "%d/%m/%Y").date()
                        if dados.get('data_inicio'): st.session_state.data_inicio = datetime.datetime.strptime(dados['data_inicio'], "%d/%m/%Y").date()
                    except: pass
                    
                    if dados.get('boletos'):
                        df_t = pd.DataFrame(dados['boletos'])
                        df_t.columns = ["Mês/Ano (MM/AAAA)", "Valor Cobrado (R$)"]
                        st.session_state.df_valores_iniciais = df_t
                    st.success("✅ IA processou os dados!")
                except Exception as e: st.error(f"Erro IA: {e}")

def aplicar_tabela_cassi():
    if "CASSI" in st.session_state.tipo_contrato_select:
        st.session_state['d_2'] = 2.34
        st.session_state['d_3'] = 5.71
        st.session_state['d_4'] = 31.37
        st.session_state['d_5'] = 6.75
        st.session_state['d_6'] = 12.47
        st.session_state['d_7'] = 43.55
        st.session_state['d_8'] = 14.42
        st.session_state['d_9'] = 27.72
        st.session_state['d_10'] = 67.57
    else:
        for f_id in range(2, 11):
            st.session_state[f'd_{f_id}'] = 0.0

# --- TELA PRINCIPAL ---
st.title("⚖️ Sistema Revisional - HSA Advogados")
df_fipe_global = obter_fipe_saude()

if df_fipe_global is not None:
    st.header("1. Dados do Processo e Contrato")
    
    tipo_contrato = st.selectbox(
        "Tipo de Contrato:", 
        ["OUTROS", "CASSI FAMÍLIA I", "CASSI FAMÍLIA II"],
        key="tipo_contrato_select",
        on_change=aplicar_tabela_cassi
    )
    
    col1, col2 = st.columns(2)
    with col1:
        parte_autora = st.text_input("Parte Autora", value=st.session_state.parte_autora)
        data_nascimento = st.date_input("Nascimento do Titular", format="DD/MM/YYYY", value=st.session_state.data_nascimento, min_value=datetime.date(1900, 1, 1), max_value=datetime.date.today())
        data_inicio = st.date_input("Data Início Cálculo", format="DD/MM/YYYY", value=st.session_state.data_inicio, min_value=datetime.date(1990, 1, 1), max_value=datetime.date.today())
        mes_reajuste = st.number_input("Mês de Reajuste", min_value=1, max_value=12, value=7)
    with col2:
        parte_re = st.text_input("Parte Ré", value="CASSI - CAIXA DE ASSISTENCIA DOS FUNCIONARIOS DO BANCO DO BRASIL" if "CASSI" in tipo_contrato else "")
        data_fim = st.date_input("Data Fim do Cálculo", format="DD/MM/YYYY", min_value=datetime.date(1990, 1, 1))
        valor_inicial = st.number_input("Valor Inicial (R$)", min_value=0.0, value=st.session_state.valor_inicial, format="%.2f")
        data_base_prescricao = st.date_input("Data base Prescrição (3 anos)", format="DD/MM/YYYY", value=datetime.date.today(), min_value=datetime.date(1990, 1, 1))

    st.header("2. Evolução dos Boletos")
    df_valores_editado = st.data_editor(st.session_state.df_valores_iniciais, num_rows="dynamic", use_container_width=True, column_config={"Valor Cobrado (R$)": st.column_config.NumberColumn("Valor Cobrado (R$)", format="R$ %.2f", min_value=0.0)})

    st.header("3. Parâmetros Legais (Devido)")
    reajustes_idade_devido = {}
    faixas = [(2, "19 a 23"), (3, "24 a 28"), (4, "29 a 33"), (5, "34 a 38"), (6, "39 a 43"), (7, "44 a 48"), (8, "49 a 53"), (9, "54 a 58"), (10, "59+")]
    cols_f = st.columns(5)
    
    for idx, (f_id, label) in enumerate(faixas):
        with cols_f[idx % 5]:
            dev = st.number_input(f"{label} (%)", min_value=0.0, format="%.2f", key=f"d_{f_id}")
            if dev > 0: reajustes_idade_devido[f_id] = dev / 100

    if st.button("Gerar Cálculo Revisional", type="primary", use_container_width=True):
        with st.spinner('Aplicando Sum Group By Year e gerando planilha...'):
            dict_valores = {str(r["Mês/Ano (MM/AAAA)"]).strip(): float(r["Valor Cobrado (R$)"]) for i, r in df_valores_editado.iterrows() if str(r["Mês/Ano (MM/AAAA)"]).strip() != "nan" and float(r["Valor Cobrado (R$)"]) > 0}

            df_raw, idades_desc = calcular_revisao_automatica(data_inicio, data_fim, data_nascimento, valor_inicial, mes_reajuste, dict_valores, reajustes_idade_devido, df_fipe_global)
            
            limite_3_anos = pd.to_datetime(data_base_prescricao) - relativedelta(years=3)
            df_restituicao = df_raw[df_raw['PERIODO_DT'] >= limite_3_anos]
            
            # --- AGRUPAMENTO SUM GROUP BY YEAR ---
            resumo_anual = []
            if not df_restituicao.empty:
                for ano, df_ano in df_restituicao.groupby(df_restituicao['PERIODO_DT'].dt.year):
                    meses = len(df_ano)
                    t_pago = df_ano['VALOR PAGO [7]'].sum()
                    t_devido = df_ano['VALOR DEVIDO [3]'].sum()
                    t_dif = df_ano['DIFERENÇA [8]'].sum()
                    resumo_anual.append([f"Ano {ano} ({meses} meses)", t_pago, t_devido, t_dif])
                    
            soma_cobrado = df_restituicao['VALOR PAGO [7]'].sum() if not df_restituicao.empty else 0
            soma_devido = df_restituicao['VALOR DEVIDO [3]'].sum() if not df_restituicao.empty else 0
            soma_diferenca = df_restituicao['DIFERENÇA [8]'].sum() if not df_restituicao.empty else 0

            # --- PREPARAÇÃO DA TABELA PARA A TELA ---
            df_tela = df_raw.copy().drop(columns=['PERIODO_DT'])
            for col in ['% FIPE SAUDE [2]', '% DO PLANO [4]']:
                df_tela[col] = df_tela[col].apply(lambda x: f"{x*100:,.2f}%".replace('.', ',') if x > 0 else "")
            for col in ['VALOR DEVIDO [3]', 'VALOR COBRADO [5]', 'VALOR PAGO [7]', 'DIFERENÇA [8]']:
                df_tela[col] = df_tela[col].apply(lambda x: f"R$ {x:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.') if x != 0 else "")

            aba_resumo, aba_detalhada = st.tabs(["📄 Resumo de Restituição", "📊 Planilha Completa"])
            
            with aba_resumo:
                if not df_restituicao.empty:
                    ano_ini, ano_fim_res = df_restituicao['PERIODO_DT'].min().year, df_restituicao['PERIODO_DT'].max().year
                    mes_ini_str = f"{MESES_COMPLETO[df_restituicao['PERIODO_DT'].min().month]}/{ano_ini}"
                    mes_fim_str = f"{MESES_COMPLETO[df_restituicao['PERIODO_DT'].max().month]}/{ano_fim_res}"
                    
                    st.markdown(f"### OS VALORES DO TOTAL CORRESPONDEM AO PERIODO DE {ano_ini} A {ano_fim_res}")
                    st.markdown(f"## TOTAL DA CAUSA: R$ {soma_diferenca:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
                    st.markdown("---")
                    st.markdown(f"#### RESUMO DE RESTITUIÇÃO {mes_ini_str} A {mes_fim_str}")
                    
                    c1, c2, c3, c4 = st.columns(4)
                    c1.write("**PERIODO EM ANOS**")
                    c2.write("**VALORES PAGO**")
                    c3.write("**VALORES DEVIDO**")
                    c4.write("**DIFERENÇAS**")
                    
                    for item in resumo_anual:
                        c1.write(item[0])
                        c2.write(f"R$ {item[1]:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
                        c3.write(f"R$ {item[2]:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
                        c4.write(f"R$ {item[3]:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
                        
                    st.markdown("---")
                    c1.write("**Últimos 3 Anos (Total)**")
                    c2.write(f"**R$ {soma_cobrado:,.2f}**".replace(',', 'X').replace('.', ',').replace('X', '.'))
                    c3.write(f"**R$ {soma_devido:,.2f}**".replace(',', 'X').replace('.', ',').replace('X', '.'))
                    c4.write(f"**R$ {soma_diferenca:,.2f}**".replace(',', 'X').replace('.', ',').replace('X', '.'))

            with aba_detalhada:
                st.dataframe(df_tela, use_container_width=True)
                
                output = BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    workbook = writer.book
                    worksheet = workbook.add_worksheet('Cálculo Revisional')
                    
                    bold_fmt = workbook.add_format({'bold': True})
                    money_fmt = workbook.add_format({'num_format': 'R$ #,##0.00'})
                    
                    worksheet.write('A1', 'PLANILHA DE CALCULO', bold_fmt)
                    worksheet.write('A3', 'PARTE AUTORA')
                    worksheet.write('D3', parte_autora)
                    worksheet.write('A4', 'PARTE RÉ')
                    worksheet.write('D4', parte_re)
                    worksheet.write('A6', f'PLANILHA DE CÁLCULOS - {parte_autora.upper()}', bold_fmt)
                    
                    colunas = df_raw.copy().drop(columns=['PERIODO_DT']).columns.tolist()
                    for col_num, value in enumerate(colunas):
                        worksheet.write(7, col_num, value, bold_fmt)
                        
                    df_excel_bruto = df_raw.copy().drop(columns=['PERIODO_DT'])
                    
                    for row_num, row_data in enumerate(df_excel_bruto.values):
                        for col_num, cell_data in enumerate(row_data):
                            if col_num in [1, 3]: 
                                txt_perc = f"{cell_data*100:,.2f}%".replace('.', ',') if cell_data > 0 else ""
                                worksheet.write(row_num + 8, col_num, txt_perc)
                            elif col_num in [2, 4, 6, 7]: 
                                worksheet.write(row_num + 8, col_num, cell_data, money_fmt)
                            else:
                                worksheet.write(row_num + 8, col_num, cell_data)
                    
                    worksheet.set_column('A:A', 15)
                    worksheet.set_column('B:I', 20)
                    
                    last_row = 7 + len(df_excel_bruto)
                    if not df_restituicao.empty:
                        worksheet.write(last_row + 2, 3, f"OS VALORES DO TOTAL CORRESPONDEM AO PERIODO DE {ano_ini} A {ano_fim_res}", bold_fmt)
                        worksheet.write(last_row + 3, 3, 'TOTAL', bold_fmt)
                        worksheet.write(last_row + 3, 7, soma_diferenca, money_fmt)
                        
                        r_row = last_row + 6
                        worksheet.write(r_row, 1, f"RESUMO DE RESTITUIÇÃO {mes_ini_str} A {mes_fim_str}", bold_fmt)
                        
                        r_row += 1
                        worksheet.write(r_row, 1, 'PERIODO EM ANOS', bold_fmt)
                        worksheet.write(r_row, 4, 'VALORES PAGO', bold_fmt)
                        worksheet.write(r_row, 6, 'VALORES DEVIDO', bold_fmt)
                        worksheet.write(r_row, 8, 'DIFERENÇAS', bold_fmt)
                        
                        r_row += 1
                        for item in resumo_anual:
                            worksheet.write(r_row, 1, item[0])
                            worksheet.write(r_row, 4, item[1], money_fmt)
                            worksheet.write(r_row, 6, item[2], money_fmt)
                            worksheet.write(r_row, 8, item[3], money_fmt)
                            r_row += 1
                            
                        worksheet.write(r_row, 1, 'Últimos 3 Anos', bold_fmt)
                        worksheet.write(r_row, 4, soma_cobrado, money_fmt)
                        worksheet.write(r_row, 6, soma_devido, money_fmt)
                        worksheet.write(r_row, 8, soma_diferenca, money_fmt)

                st.download_button("📥 Baixar Excel do Modelo Oficial", data=output.getvalue(), file_name=f"Calculo_{parte_autora}.xlsx")
                
