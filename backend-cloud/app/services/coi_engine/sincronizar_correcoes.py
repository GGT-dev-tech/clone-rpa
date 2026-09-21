"""
Sincronizador de Correções: Relatorio_Traducao_Bancaria_Atualizado.xlsx -> JSONs de mapeamento

Lê a coluna 11 (COI correto) de todas as abas do Excel e:
1. Atualiza mapeamento_fornecedores_coi.json com o COI correto para cada fornecedor
2. Atualiza regras_bancarias_coi.json com novos padrões para descrições fixas
3. Gera avisos para entradas que não foram encontradas em nenhum mapeamento
"""

import json
import re
import openpyxl
from collections import defaultdict

EXCEL_FILE = 'Relatorio_Traducao_Bancaria_Atualizado.xlsx'

def clean_bank_description(desc):
    """Remove prefixos bancários para melhorar matching."""
    prefixes = [
        r'^PG\.P/INTERNET\s*-\s*',
        r'^CREDITO\s+PIX\s*-\s*',
        r'^DEBITO\s+PIX\s*-\s*',
        r'^PIX\s*-\s*',
        r'^TED\s*-\s*',
        r'^DOC\s*-\s*',
        r'^LIQ\.COB\.\s*',
        r'^VENDA CIELO\s*',
        r'^CREDITO\s+TED\s*-\s*',
    ]
    cleaned = desc.upper().strip()
    for p in prefixes:
        cleaned = re.sub(p, '', cleaned, flags=re.IGNORECASE).strip()
    return cleaned


def normalizar_coi(valor_raw):
    """Normaliza código COI para 6 dígitos."""
    try:
        return str(int(float(str(valor_raw).strip()))).zfill(6)
    except:
        return str(valor_raw).strip().zfill(6)


def extrair_correcoes(excel_path):
    """
    Lê todas as abas do Excel e extrai:
    {desc_banco: {coi_correto, nome_sistema, aba}}
    Prioriza casos onde o COI muda (ignora onde é igual).
    """
    wb = openpyxl.load_workbook(excel_path)
    correcoes = {}
    
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        if ws.max_column < 11:
            continue
        
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or len(row) < 11 or row[10] is None:
                continue
            
            desc_banco = str(row[1]).strip() if row[1] else ''
            nome_sistema = str(row[4]).strip() if row[4] else ''
            if not desc_banco:
                continue
            
            coi_correto = normalizar_coi(row[10])
            
            # Só registrar se ainda não temos essa descrição, ou se esta aba tem info mais nova
            if desc_banco not in correcoes:
                correcoes[desc_banco] = {
                    'coi_correto': coi_correto,
                    'nome_sistema': nome_sistema,
                    'aba': sheet_name
                }
    
    return correcoes


def atualizar_mapeamento(mapeamento, correcoes, coi_data):
    """
    Para cada descrição do banco com correção, tenta encontrar o fornecedor 
    no mapeamento e atualiza o COI.
    """
    atualizados = 0
    adicionados = 0
    nao_encontrados = []
    
    # Construir índice inverso: nome_identificado -> cod_fornecedor
    indice_nome = {}
    for cod, info in mapeamento.items():
        nome = info.get('nome_identificado', '').upper()
        indice_nome[nome] = cod
    
    # Construir índice de descrições COI para lookup
    coi_descricoes = {c['codigo']: c.get('descricao', '') for c in coi_data} if coi_data else {}
    
    for desc_banco, info_corr in correcoes.items():
        coi_novo = info_corr['coi_correto']
        nome_sistema = info_corr['nome_sistema']
        
        # Limpar a descrição do banco para tentar match com nome_identificado
        desc_clean = clean_bank_description(desc_banco)
        
        encontrado = False
        
        # 1. Tentar match direto pelo nome_sistema no mapeamento
        if nome_sistema and nome_sistema.upper() in indice_nome:
            cod = indice_nome[nome_sistema.upper()]
            if mapeamento[cod]['codigo_coi'] != coi_novo:
                mapeamento[cod]['codigo_coi'] = coi_novo
                # Atualizar descrição do COI se disponível
                if coi_novo in coi_descricoes:
                    mapeamento[cod]['descricao_coi'] = coi_descricoes[coi_novo]
                mapeamento[cod]['revisado'] = True
                atualizados += 1
            encontrado = True
        
        # 2. Tentar match pelo desc_clean contra nome_identificado
        if not encontrado:
            for cod, info in mapeamento.items():
                nome_id = info.get('nome_identificado', '').upper()
                if desc_clean in nome_id or nome_id in desc_clean:
                    if mapeamento[cod]['codigo_coi'] != coi_novo:
                        mapeamento[cod]['codigo_coi'] = coi_novo
                        if coi_novo in coi_descricoes:
                            mapeamento[cod]['descricao_coi'] = coi_descricoes[coi_novo]
                        mapeamento[cod]['revisado'] = True
                        atualizados += 1
                    encontrado = True
                    break
        
        if not encontrado:
            nao_encontrados.append({
                'desc_banco': desc_banco,
                'coi_correto': coi_novo,
                'nome_sistema': nome_sistema
            })
    
    return mapeamento, atualizados, adicionados, nao_encontrados


def atualizar_regras_bancarias(regras_bancarias, correcoes, coi_data):
    """
    Para descrições bancárias fixas (não vinculadas a fornecedor),
    cria ou atualiza regras bancárias no JSON.
    """
    coi_descricoes = {c['codigo']: c.get('descricao', '') for c in coi_data} if coi_data else {}
    
    # Construir índice de regras existentes por padrão
    padroes_existentes = {r['padrao']: i for i, r in enumerate(regras_bancarias)}
    
    atualizados = 0
    adicionados = 0
    
    for desc_banco, info_corr in correcoes.items():
        coi_novo = info_corr['coi_correto']
        
        # Criar padrão escapado para regex
        desc_escaped = re.escape(desc_banco.strip())
        
        # Verificar se já existe uma regra com esse padrão exato
        if desc_escaped in padroes_existentes:
            idx = padroes_existentes[desc_escaped]
            if regras_bancarias[idx]['codigo_coi'] != coi_novo:
                regras_bancarias[idx]['codigo_coi'] = coi_novo
                if coi_novo in coi_descricoes:
                    regras_bancarias[idx]['descricao_coi'] = coi_descricoes[coi_novo]
                regras_bancarias[idx]['tipo'] = 'manual'
                atualizados += 1
        else:
            # Adicionar nova regra bancária
            nova_regra = {
                'padrao': desc_escaped,
                'codigo_coi': coi_novo,
                'descricao_coi': coi_descricoes.get(coi_novo, f'COI {coi_novo}'),
                'tipo': 'manual'
            }
            regras_bancarias.append(nova_regra)
            padroes_existentes[desc_escaped] = len(regras_bancarias) - 1
            adicionados += 1
    
    return regras_bancarias, atualizados, adicionados


def main():
    print('=' * 60)
    print('Sincronizador de Correções do Excel -> JSONs')
    print('=' * 60)
    
    # 1. Extrair correções do Excel
    print(f'\n[1/4] Lendo correções de {EXCEL_FILE}...')
    correcoes = extrair_correcoes(EXCEL_FILE)
    print(f'  -> {len(correcoes)} descrições com COI definido pelo usuário')
    
    # 2. Carregar dados base
    print('\n[2/4] Carregando arquivos de mapeamento...')
    with open('mapeamento_fornecedores_coi.json', 'r', encoding='utf-8') as f:
        mapeamento = json.load(f)
    print(f'  -> mapeamento_fornecedores_coi.json: {len(mapeamento)} entradas')
    
    with open('regras_bancarias_coi.json', 'r', encoding='utf-8') as f:
        regras_bancarias = json.load(f)
    print(f'  -> regras_bancarias_coi.json: {len(regras_bancarias)} regras')
    
    # Carregar COI data para nomes das descrições
    coi_data = []
    try:
        with open('coi_data.json', 'r', encoding='utf-8') as f:
            coi_data = json.load(f)
        print(f'  -> coi_data.json: {len(coi_data)} COIs')
    except:
        print('  [AVISO] coi_data.json não encontrado, descrições COI não serão atualizadas')
    
    # 3. Atualizar mapeamento de fornecedores
    print('\n[3/4] Atualizando mapeamento de fornecedores...')
    mapeamento, atualiz_map, adicion_map, nao_enc = atualizar_mapeamento(mapeamento, correcoes, coi_data)
    print(f'  -> Atualizados: {atualiz_map} | Não encontrados no mapeamento: {len(nao_enc)}')
    
    # 4. Atualizar regras bancárias com os não encontrados no mapeamento
    print('\n[4/4] Atualizando regras bancárias...')
    # Para os não encontrados no mapeamento de fornecedores, adicionar como regra bancária
    correcoes_bancarias = {item['desc_banco']: {'coi_correto': item['coi_correto'], 'nome_sistema': item['nome_sistema']} 
                           for item in nao_enc}
    # Também processar TODOS como regra bancária (para garantir cobertura máxima)
    regras_bancarias, atualiz_rb, adicion_rb = atualizar_regras_bancarias(regras_bancarias, correcoes, coi_data)
    print(f'  -> Atualizadas: {atualiz_rb} | Novas regras adicionadas: {adicion_rb}')
    
    # Salvar arquivos
    with open('mapeamento_fornecedores_coi.json', 'w', encoding='utf-8') as f:
        json.dump(mapeamento, f, indent=2, ensure_ascii=False)
    print('\n✅ mapeamento_fornecedores_coi.json salvo')
    
    with open('regras_bancarias_coi.json', 'w', encoding='utf-8') as f:
        json.dump(regras_bancarias, f, indent=2, ensure_ascii=False)
    print('✅ regras_bancarias_coi.json salvo')
    
    # Relatório final
    print('\n' + '=' * 60)
    print('RESUMO')
    print('=' * 60)
    print(f'Correções lidas do Excel           : {len(correcoes)}')
    print(f'Fornecedores atualizados           : {atualiz_map}')
    print(f'Regras bancárias atualizadas       : {atualiz_rb}')
    print(f'Novas regras bancárias adicionadas : {adicion_rb}')
    print(f'Regras bancárias totais agora      : {len(regras_bancarias)}')
    
    if nao_enc:
        print(f'\n[AVISO] {len(nao_enc)} descrições não encontradas no mapeamento de fornecedores')
        print('(Foram adicionadas como regras bancárias)')
        # Listar primeiras 10
        for item in nao_enc[:10]:
            print(f"  - \"{item['desc_banco']}\" -> COI {item['coi_correto']}")
    
    print('\nSincronização concluída!')


if __name__ == '__main__':
    main()
