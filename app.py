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

st.set_page_config(page_title="SaaS - Revisão Plano de Saúde", layout="wide")

# --- MEMÓRIA DO SISTEMA ---
if 'parte_autora' not in st.session_state: st.session_state.parte_autora = ""
if 'valor_inicial' not in st.session_state: st.session_state.valor_inicial = 0.0
if 'data_nascimento' not in st.session_state: st.session_state.data_nascimento = datetime.date(1970, 1, 1)
if 'data_inicio' not in st.session_state: st.session_state.data_inicio = datetime.date(2015, 1, 1)
if 'df_valores_iniciais' not in st.session_state: 
    st.session_state.df_valores_iniciais = pd.DataFrame({"Mês/Ano (MM/AAAA)": [""], "Valor Cobrado (R$)": [0.0]})

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
        valor_anterior_cobrado = valor_cobrado
        
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

# --- MENU LATERAL: INTELIGÊNCIA ARTIFICIAL E CHAT ---
with st.sidebar:
    st.header("🤖 Assistente de IA")
    api_key = st.text_input("Sua Chave API do Gemini", type="password")
    
    st.markdown("💬 **Converse com a IA:** Digite as informações do cliente (Ex: Nasceu em 1952, valor inicial de 800...)")
    texto_ia = st.text_area("Instruções em texto:", height=100)
    
    st.markdown("📄 **Ou arraste os PDFs/Imagens:**")
    arquivos_enviados = st.file_uploader("Documentos", accept_multiple_files=True, type=['pdf', 'png', 'jpg', 'jpeg'])
    
    if st.button("Processar Dados com IA", type="primary", use_container_width=True):
        if not api_key:
            st.error("Insira a sua Chave API do Gemini primeiro.")
        elif not arquivos_enviados and not texto_ia:
            st.error("Envie um arquivo ou digite um texto para a IA processar.")
        else:
            with st.spinner("A IA está extraindo as informações..."):
                try:
                    genai.configure(api_key=api_key)
                    modelos_disponiveis = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                        
                    nome_modelo_ia = None
                    for termo in ["gemini-2", "gemini-1.5", "gemini-flash"]:
                        for m_name in modelos_disponiveis:
                            if termo in m_name:
                                nome_modelo_ia = m_name
                                break
                        if nome_modelo_ia: break
                            
                    if not nome_modelo_ia: nome_modelo_ia = modelos_disponiveis[0]
                    modelo = genai.GenerativeModel(nome_modelo_ia.replace("models/", ""))
                    
                    conteudo_para_ia = []
                    
                    # 1. Adiciona o texto do chat
                    prompt_ia = f"""
                    Você é um perito financeiro do escritório HSA Advogados analisando dados de plano de saúde.
                    Extraia os dados dos documentos anexados E/OU das instruções em texto abaixo:
                    INSTRUÇÕES DE TEXTO DO USUÁRIO: "{texto_ia}"
                    
                    Devolva APENAS um objeto JSON no formato exato abaixo, sem markdown:
                    {{
                      "parte_autora": "Nome da Pessoa",
                      "data_nascimento": "DD/MM/AAAA",
                      "data_inicio": "DD/MM/AAAA",
                      "valor_primeiro_boleto": 1500.50,
                      "boletos": [
                        {{"mes_ano": "01/2022", "valor": 1500.50}}
                      ]
                    }}
                    Se não encontrar datas, devolva null nos campos de data.
                    """
                    conteudo_para_ia.append(prompt_ia)
                    
                    # 2. Adiciona os arquivos
                    arquivos_temporarios = []
                    for arquivo in arquivos_enviados:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{arquivo.name.split('.')[-1]}") as temp_file:
                            temp_file.write(arquivo.read())
                            temp_file_path = temp_file.name
                        
                        arq_subido = genai.upload_file(temp_file_path)
                        conteudo_para_ia.append(arq_subido)
                        arquivos_temporarios.append(arq_subido)
                        os.unlink(temp_file_path)
                    
                    # Gera a resposta
                    resposta_ia = modelo.generate_content(conteudo_para_ia)
                    
                    # Limpeza
                    for a in arquivos_temporarios: genai.delete_file(a.name)
                    
                    texto_json = resposta_ia.text.replace("```json", "").replace("```", "").strip()
                    dados_extraidos = json.loads(texto_json)
                    
                    # Preenche os campos
                    if dados_extraidos.get('parte_autora'): st.session_state.parte_autora = dados_extraidos['parte_autora']
                    if dados_extraidos.get('valor_primeiro_boleto'): st.session_state.valor_inicial = float(dados_extraidos['valor_primeiro_boleto'])
                    
                    try:
                        if dados_extraidos.get('data_nascimento'):
                            st.session_state.data_nascimento = datetime.datetime.strptime(dados_extraidos['data_nascimento'], "%d/%m/%Y").date()
                        if dados_extraidos.get('data_inicio'):
                            st.session_state.data_inicio = datetime.datetime.strptime(dados_extraidos['data_inicio'], "%d/%m/%Y").date()
                    except Exception as e: pass
                    
                    lista_boletos = dados_extraidos.get('boletos', [])
                    if lista_boletos:
                        df_temp = pd.DataFrame(lista_boletos)
                        df_temp.columns = ["Mês/Ano (MM/AAAA)", "Valor Cobrado (R$)"]
                        st.session_state.df_valores_iniciais = df_temp
                    
                    st.success("✅ IA processou os dados com sucesso!")
                except Exception as e:
                    st.error(f"Erro na IA: {e}")

# --- TELA PRINCIPAL ---
st.title("⚖️ Sistema Revisional Inteligente - Planos de Saúde")
df_fipe_global = obter_fipe_saude()

if df_fipe_global is not None:
    st.header("1. Dados do Processo")
    col1, col2 = st.columns(2)
    with col1:
        parte_autora = st.text_input("Parte Autora", value=st.session_state.parte_autora)
        data_nascimento = st.date_input("Data de Nascimento do Titular", format="DD/MM/YYYY", value=st.session_state.data_nascimento, min_value=datetime.date(1900, 1, 1), max_value=datetime.date.today())
        data_inicio = st.date_input("Data de Início do Cálculo", format="DD/MM/YYYY", value=st.session_state.data_inicio, min_value=datetime.date(1990, 1, 1), max_value=datetime.date.today())
        mes_reajuste = st.number_input("Mês de Reajuste (Aniversário Contrato)", min_value=1, max_value=12, value=7)
    with col2:
        parte_re = st.text_input("Parte Ré (Ex: CASSI)")
        data_fim = st.date_input("Data Fim do Cálculo", format="DD/MM/YYYY", min_value=datetime.date(1990, 1, 1))
        valor_inicial = st.number_input("Valor Inicial (Primeiro Mês R$)", min_value=0.0, value=st.session_state.valor_inicial, format="%.2f")
        data_base_prescricao = st.date_input("Data para Prescrição (3 anos p/ trás)", format="DD/MM/YYYY", value=datetime.date.today(), min_value=datetime.date(1990, 1, 1))

    st.markdown("---")
    st.header("2. Evolução dos Boletos (Preenchimento Automático ou Manual)")
    df_valores_editado = st.data_editor(
        st.session_state.df_valores_iniciais, 
        num_rows="dynamic", 
        use_container_width=True,
        column_config={"Valor Cobrado (R$)": st.column_config.NumberColumn("Valor Cobrado (R$)", format="R$ %.2f", min_value=0.0)}
    )

    st.markdown("---")
    st.header("3. Parâmetros Legais (O que é Devido)")
    reajustes_idade_devido = {}
    faixas = [(2, "19 a 23"), (3, "24 a 28"), (4, "29 a 33"), (5, "34 a 38"), (6, "39 a 43"), (7, "44 a 48"), (8, "49 a 53"), (9, "54 a 58"), (10, "59+")]
    
    cols_f = st.columns(5)
    for idx, (faixa_id, label) in enumerate(faixas):
        with cols_f[idx % 5]:
            dev = st.number_input(f"{label} (%)", min_value=0.0, value=0.0, format="%.2f", key=f"d_{faixa_id}")
            if dev > 0: reajustes_idade_devido[faixa_id] = dev / 100

    st.markdown("---")
    if st.button("Gerar Cálculo Revisional Completo", type="primary", use_container_width=True):
        with st.spinner('Gerando perícia atuarial...'):
            dict_valores = {}
            for index, row in df_valores_editado.iterrows():
                try:
                    mes_ano = str(row["Mês/Ano (MM/AAAA)"]).strip()
                    val = float(row["Valor Cobrado (R$)"])
                    if mes_ano != "nan" and val > 0:
                        dict_valores[mes_ano] = val
                except:
                    pass

            df_raw, idades_descobertas = calcular_revisao_automatica(
                data_inicio, data_fim, data_nascimento, valor_inicial, mes_reajuste, 
                dict_valores, reajustes_idade_devido, df_fipe_global
            )
            
            # Cálculo de Prescrição Trienal
            limite_3_anos = pd.to_datetime(data_base_prescricao) - relativedelta(years=3)
            df_restituicao = df_raw[df_raw['PERIODO_DT'] >= limite_3_anos]
            
            soma_cobrado = soma_devido = soma_diferenca = 0
            if not df_restituicao.empty:
                soma_cobrado = df_restituicao['VALOR COBRADO'].sum()
                soma_devido = df_restituicao['VALOR DEVIDO'].sum()
                soma_diferenca = df_restituicao['DIFERENÇA'].sum()

            # --- SISTEMA DE ABAS (RESUMO E PLANILHA) ---
            st.header("4. Resultados Oficiais")
            aba_resumo, aba_detalhada = st.tabs(["📄 Resumo de Restituição", "📊 Planilha Completa (Mês a Mês)"])
            
            # ABA 1: RESUMO (Estilo da imagem enviada)
            with aba_resumo:
                st.subheader(f"Resumo de Restituição - {parte_autora}")
                if not df_restituicao.empty:
                    mes_inicio_res = df_restituicao['PERIODO_DT'].min().strftime('%m/%Y')
                    mes_fim_res = df_restituicao['PERIODO_DT'].max().strftime('%m/%Y')
                    st.markdown(f"**Período Apurado:** {mes_inicio_res} a {mes_fim_res}")
                
                col_res1, col_res2, col_res3 = st.columns(3)
                col_res1.metric("Valor Pago (Cobrado)", f"R$ {soma_cobrado:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
                col_res2.metric("Valor Devido (Legal)", f"R$ {soma_devido:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
                col_res3.metric("Total a Restituir (Indébito)", f"R$ {soma_diferenca:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
                
                # Validação ANS
                st.markdown("---")
                st.write("**Análise de Legalidade (Res. 63/03 ANS):**")
                if idades_descobertas:
                    preco_proj = {1: 1.0}
                    for f in range(2, 11): preco_proj[f] = preco_proj[f-1] * (1 + idades_descobertas.get(f, 0.0))
                    if preco_proj[10] > (preco_proj[1] * 6.0001) or (preco_proj[10] / preco_proj[7] if 7 in preco_proj else 0) > ((preco_proj[7] / preco_proj[1] if 7 in preco_proj else 0) + 0.0001):
                        st.error("🚨 ALERTA: A evolução das faixas etárias cobradas pela operadora quebra os limites matemáticos da ANS.")
                    else:
                        st.success("✅ A variação de faixa etária está de acordo com as regras da ANS.")

            # ABA 2: DETALHADA E EXPORTAÇÃO EXCEL
            with aba_detalhada:
                df_display = df_raw.copy().drop(columns=['PERIODO_DT'])
                for col in ['% APLICADO PLANO', '% FIPE (LEGAL)', '% FAIXA (LEGAL)']:
                    df_display[col] = df_display[col].apply(lambda x: f"{x*100:,.2f}%".replace('.', ',') if x > 0 else "-")
                for col in ['VALOR ANT. COBRADO', 'VALOR DEVIDO', 'VALOR COBRADO', 'DIFERENÇA']:
                    df_display[col] = df_display[col].apply(lambda x: f"{x:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))

                st.dataframe(df_display, use_container_width=True)
                
                # --- MOTOR DE EXPORTAÇÃO EXCEL PROFISSIONAL (Estilo Modelo) ---
                output = BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    workbook = writer.book
                    worksheet = workbook.add_worksheet('Cálculo Revisional')
                    writer.sheets['Cálculo Revisional'] = worksheet
                    
                    # Formatadores
                    bold_format = workbook.add_format({'bold': True})
                    header_format = workbook.add_format({'bold': True, 'bg_color': '#D3D3D3', 'border': 1})
                    
                    # Cabeçalho da Petição (Com o nome do escritório)
                    worksheet.write('A1', 'HSA ADVOGADOS - PLANILHA DE CÁLCULO REVISIONAL', bold_format)
                    worksheet.write('A3', 'PARTE AUTORA:', bold_format)
                    worksheet.write('B3', parte_autora)
                    worksheet.write('A4', 'PARTE RÉ:', bold_format)
                    worksheet.write('B4', parte_re)
                    worksheet.write('A6', f'PLANILHA DE CÁLCULOS - {parte_autora.upper()}', bold_format)
                    
                    # Escrevendo a tabela de dados
                    df_display.to_excel(writer, index=False, sheet_name='Cálculo Revisional', startrow=7)
                    
                    # Aplicando estilo na linha de títulos da tabela
                    for col_num, value in enumerate(df_display.columns.values):
                        worksheet.write(7, col_num, value, header_format)
                    
                    # Ajustando a largura das colunas
                    worksheet.set_column('A:B', 12)
                    worksheet.set_column('C:K', 18)
                    
                    # Inserindo o Quadro de Resumo no final da tabela no Excel
                    linha_final = 7 + len(df_display) + 3
                    if not df_restituicao.empty:
                        worksheet.write(linha_final, 1, 'RESUMO DE RESTITUIÇÃO', bold_format)
                        worksheet.write(linha_final + 1, 1, 'VALOR PAGO', bold_format)
                        worksheet.write(linha_final + 1, 2, f"R$ {soma_cobrado:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
                        worksheet.write(linha_final + 2, 1, 'VALOR DEVIDO', bold_format)
                        worksheet.write(linha_final + 2, 2, f"R$ {soma_devido:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
                        worksheet.write(linha_final + 3, 1, 'DIFERENÇA (INDÉBITO)', bold_format)
                        worksheet.write(linha_final + 3, 2, f"R$ {soma_diferenca:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))

                st.download_button("📥 Baixar Excel Organizado", data=output.getvalue(), file_name=f"Calculo_HSA_{parte_autora}.xlsx")
                    
