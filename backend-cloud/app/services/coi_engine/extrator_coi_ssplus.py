"""
=======================================================================
EXTRATOR E MAPEADOR DE CÓDIGOS COI (SS+)  v1.0
=======================================================================
Autor   : Gerado via Antigravity (Google DeepMind)
Versão  : 1.0.0

COMO USAR:
  1. Certifique-se de ter os PDFs na mesma pasta (ou especifique o caminho):
       - codigos coi.pdf
       - contas-pagas-01-01-2025.pdf
  2. Execute no terminal:
       python3 extrator_coi_ssplus.py
  3. O script irá gerar:
       - coi_data.json (todos os códigos contábeis)
       - contas_pagas_data.json (todos os pagamentos extraídos)
       - mapeamento_fornecedores_coi.json (O DICIONÁRIO DE MAPEAMENTO)

Dependências:  pip install pdfplumber
=======================================================================
"""

import pdfplumber
import re
import json
import os

def extrair_coi(pdf_path):
    print(f"Extraindo Códigos COI de: {pdf_path}")
    coi_list = []
    # Padrão: 6 dígitos, descrição (guloso), D/C e opcionalmente a conta contábil
    pattern = re.compile(r'^(\d{6})\s+(.*)\s+([DC])(?:\s+(.*))?$')
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ''
                for line in text.split('\n'):
                    line = line.strip()
                    if not line or len(line) < 6: continue
                    m = pattern.match(line)
                    if m:
                        coi_list.append({
                            'codigo': m.group(1),
                            'descricao': m.group(2).strip(),
                            'tipo': m.group(3),
                            'conta_contabil': m.group(4).strip()
                        })
        print(f"  -> {len(coi_list)} códigos extraídos.")
        return coi_list
    except FileNotFoundError:
        print(f"  -> Erro: Arquivo {pdf_path} não encontrado.")
        return []

def extrair_contas_pagas(pdf_path):
    print(f"Extraindo Contas Pagas de: {pdf_path}")
    cp_list = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ''
                buffer = ""
                for line in text.split('\n'):
                    line = line.strip()
                    if not line: continue
                    
                    # Ignorar cabeçalhos
                    if line.startswith('===') or line.startswith('---') or line.startswith('Duplic Valor') or line.startswith('Vencimento Empr'):
                        continue
                    if line.startswith('0001 - PONTUAL') or line.startswith('Data:') or line.startswith('PCFNPAG0') or line.startswith('Nro:'):
                        continue
                    if line.startswith('FINANCEIRO') or line.startswith('Pagina'):
                        continue
                        
                    buffer += " " + line if buffer else line
                    
                    # Verifica se o buffer termina com Data (xx/xx/xxxx) e Empresa (xxxx)
                    m_end = re.search(r'(\d{2}/\d{2}/\d{4})\s+(\d{4})$', buffer)
                    if m_end:
                        # Extrai as partes principais da linha
                        m_start = re.match(r'^(\S+)\s+([\d,\.]+)\s+(\d+)\s+(.+?)\s+(\d{2}/\d{2}/\d{4})\s+(\d{4})$', buffer)
                        if m_start:
                            cp_list.append({
                                'duplicata': m_start.group(1),
                                'valor': m_start.group(2),
                                'fornecedor_codigo': m_start.group(3),
                                'fornecedor_nome_original': m_start.group(4).strip(),
                                'vencimento': m_start.group(5),
                                'empresa': m_start.group(6)
                            })
                        else:
                            parts = buffer.split()
                            if len(parts) >= 6:
                                cp_list.append({
                                    'duplicata': parts[0],
                                    'valor': parts[1],
                                    'fornecedor_codigo': parts[2],
                                    'fornecedor_nome_original': ' '.join(parts[3:-2]),
                                    'vencimento': parts[-2],
                                    'empresa': parts[-1]
                                })
                        buffer = ""
        print(f"  -> {len(cp_list)} contas pagas extraídas.")
        return cp_list
    except FileNotFoundError:
        print(f"  -> Erro: Arquivo {pdf_path} não encontrado.")
        return []

def sugerir_codigo_coi(nome_fornecedor, regras):
    """
    Usa o arquivo de regras configurável para sugerir o COI via palavras-chave (com word boundary).
    """
    nome = nome_fornecedor.upper()
    
    for cod_coi, info in regras.items():
        palavras = info.get("palavras_chave", [])
        for p in palavras:
            p_upper = p.upper()
            # Garante match de palavra inteira usando regex
            # Ex: "ISS " ou " ISS " ou inicio/fim
            pattern = r'\b' + re.escape(p_upper) + r'\b'
            if re.search(pattern, nome):
                return cod_coi
    
    # Retorna o padrão de pagamento a fornecedores
    return '000043' 

def criar_dicionario_mapeamento(cp_list, coi_list, regras):
    print("Criando dicionário de mapeamento de fornecedores baseado em condições...")
    dicionario = {}
    
    for cp in cp_list:
        cod = cp['fornecedor_codigo']
        nome = cp['fornecedor_nome_original']
        
        if cod not in dicionario:
            sugestao_coi = sugerir_codigo_coi(nome, regras)
            
            # Busca a descrição do COI sugerido
            desc_coi = "Não encontrado"
            for c in coi_list:
                if c['codigo'] == sugestao_coi:
                    desc_coi = c['descricao']
                    break
                    
            dicionario[cod] = {
                'nome_identificado': nome,
                'codigo_coi': sugestao_coi,
                'descricao_coi': desc_coi,
                'revisado': False # Flag para você marcar como 'True' quando validar
            }
            
    print(f"  -> Dicionário criado com {len(dicionario)} fornecedores únicos.")
    return dicionario

def main():
    # Defina aqui os caminhos para os PDFs
    pdf_coi = 'codigos coi.pdf'
    pdf_contas = 'contas-pagas-01-01-2025.pdf'
    
    coi = extrair_coi(pdf_coi)
    cp = extrair_contas_pagas(pdf_contas)
    
    if coi and cp:
        # Tentar carregar arquivo de regras de fornecedores
        regras = {}
        if os.path.exists('regras_fornecedores_coi.json'):
            with open('regras_fornecedores_coi.json', 'r', encoding='utf-8') as f:
                regras = json.load(f)
        else:
            print("Aviso: arquivo 'regras_fornecedores_coi.json' não encontrado. Recomenda-se gerar via gerador_regras_coi.py")

        # Salva dados brutos (opcional, bom para debug/conferência)
        with open('coi_data.json', 'w', encoding='utf-8') as f:
            json.dump(coi, f, indent=2, ensure_ascii=False)
        with open('contas_pagas_data.json', 'w', encoding='utf-8') as f:
            json.dump(cp, f, indent=2, ensure_ascii=False)
            
        # Cria e salva o dicionário usando as regras inteligentes
        dicionario = criar_dicionario_mapeamento(cp, coi, regras)
        with open('mapeamento_fornecedores_coi.json', 'w', encoding='utf-8') as f:
            json.dump(dicionario, f, indent=2, ensure_ascii=False)
            
        print("\nProcesso finalizado com sucesso!")
        print("Revise o arquivo 'mapeamento_fornecedores_coi.json' para validar os códigos COI.")

if __name__ == '__main__':
    main()
