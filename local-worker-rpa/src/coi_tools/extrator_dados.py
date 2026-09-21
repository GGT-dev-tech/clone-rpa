import pdfplumber
import re
import json
import os

def extrair_coi(pdf_path):
    coi_list = []
    # Pattern to match 6 digits, description, D/C type, and optional accounting code
    pattern = re.compile(r'^(\d{6})\s+(.*?)\s+([DC])\s*(.*)$')
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ''
            for line in text.split('\n'):
                line = line.strip()
                if not line or len(line) < 6: continue
                m = pattern.match(line)
                if m:
                    codigo = m.group(1)
                    descricao = m.group(2).strip()
                    tipo = m.group(3)
                    conta = m.group(4).strip()
                    coi_list.append({
                        'codigo': codigo,
                        'descricao': descricao,
                        'tipo': tipo,
                        'conta_contabil': conta
                    })
    return coi_list

def extrair_contas_pagas(pdf_path):
    cp_list = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ''
            buffer = ""
            for line in text.split('\n'):
                line = line.strip()
                if not line: continue
                
                # Skip headers
                if line.startswith('===') or line.startswith('---') or line.startswith('Duplic Valor') or line.startswith('Vencimento Empr'):
                    continue
                if line.startswith('0001 - PONTUAL') or line.startswith('Data:') or line.startswith('PCFNPAG0') or line.startswith('Nro:'):
                    continue
                if line.startswith('FINANCEIRO') or line.startswith('Pagina'):
                    continue
                    
                buffer += " " + line if buffer else line
                
                # Check if buffer ends with date and 4 digits (Empresa)
                m = re.search(r'(\d{2}/\d{2}/\d{4})\s+(\d{4})$', buffer)
                if m:
                    # We have a full entry. Let's parse it.
                    # Format: Duplicata Valor Forn_Cod Fornecedor_Nome [Observacao] Data Empr
                    m_start = re.match(r'^(\S+)\s+([\d,\.]+)\s+(\d+)\s+(.+?)\s+(\d{2}/\d{2}/\d{4})\s+(\d{4})$', buffer)
                    if m_start:
                        cp_list.append({
                            'duplicata': m_start.group(1),
                            'valor': m_start.group(2),
                            'fornecedor_codigo': m_start.group(3),
                            'fornecedor_nome': m_start.group(4).strip(),
                            'vencimento': m_start.group(5),
                            'empresa': m_start.group(6)
                        })
                    else:
                        # Maybe observation is inside
                        parts = buffer.split()
                        if len(parts) >= 6:
                            cp_list.append({
                                'duplicata': parts[0],
                                'valor': parts[1],
                                'fornecedor_codigo': parts[2],
                                'fornecedor_nome': ' '.join(parts[3:-2]), # rough approximation
                                'vencimento': parts[-2],
                                'empresa': parts[-1]
                            })
                    buffer = ""
    return cp_list

if __name__ == '__main__':
    import glob
    coi = extrair_coi('codigos coi.pdf')
    print(f"Extracted {len(coi)} COI codes.")
    
    todas_contas = []
    for pdf_file in glob.glob('contas*.pdf'):
        cp = extrair_contas_pagas(pdf_file)
        print(f"Extracted {len(cp)} Contas from {pdf_file}.")
        todas_contas.extend(cp)
        
    print(f"Total Contas extracted: {len(todas_contas)}")
    
    with open('coi_data.json', 'w') as f:
        json.dump(coi, f, indent=2, ensure_ascii=False)
        
    with open('contas_pagas_data.json', 'w') as f:
        json.dump(todas_contas, f, indent=2, ensure_ascii=False)
