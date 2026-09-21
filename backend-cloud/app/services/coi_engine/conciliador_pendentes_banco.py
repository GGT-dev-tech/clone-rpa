"""
Motor de Conciliação: Contas Pendentes SS+ → Extrato Bancário

Fluxo:
  1. Lê as contas pendentes do sistema (contas_pagas_data.json)
  2. Agrupa por mês de vencimento
  3. Para cada mês, carrega o extrato bancário correspondente
  4. Tenta cruzar cada pendência com um lançamento no extrato
  5. Gera relatório Excel por mês com status: ENCONTRADO / PARCIAL / NÃO ENCONTRADO

Saída: relatorios_mensais/Conciliacao_Pendentes_<Mes>_<Ano>.xlsx
"""

import csv
import json
import re
import os
import glob
import difflib
import calendar
from collections import defaultdict
from datetime import datetime, timedelta
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment


# ------------------------------------------------------------------
# Utilitários
# ------------------------------------------------------------------

def parse_valor_br(valor_str):
    if not isinstance(valor_str, str):
        return float(valor_str) if valor_str else 0.0
    v = valor_str.strip()
    is_negative = v.startswith('-')
    v = re.sub(r'[^\d,.-]', '', v)
    if '.' in v and ',' in v:
        v = v.replace('.', '').replace(',', '.')
    elif ',' in v:
        v = v.replace(',', '.')
    try:
        val = float(v)
        return -abs(val) if is_negative else abs(val)
    except:
        return 0.0


def parse_date(date_str):
    """Converte DD/MM/YYYY para objeto datetime."""
    try:
        return datetime.strptime(date_str.strip(), '%d/%m/%Y')
    except:
        return None


def clean_bank_description(desc):
    """Remove prefixos bancários comuns para melhorar o match."""
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
    cleaned = desc.upper()
    for p in prefixes:
        cleaned = re.sub(p, '', cleaned, flags=re.IGNORECASE).strip()
    return cleaned


def nome_sistema_from_codigo(cod_fornecedor, mapeamento):
    """Retorna o nome do sistema para um código de fornecedor."""
    info = mapeamento.get(cod_fornecedor)
    if info:
        return info.get('nome_identificado', '')
    return ''


def match_supplier_by_name(nome_fornecedor_sistema, transacoes_extrato):
    """
    Para um dado nome de fornecedor do sistema, busca transações no extrato
    que correspondam. Retorna lista de transações compatíveis.
    """
    nome_clean = nome_fornecedor_sistema.upper().strip()
    # Limpar o nome do sistema também (remover números de matrícula, tracinhos, etc)
    nome_clean_short = re.sub(r'[-\s]+MATR\w+.*', '', nome_clean).strip()
    resultados = []
    for t in transacoes_extrato:
        desc_clean = clean_bank_description(t['descricao'])
        # Substring direto (mais forte)
        if desc_clean in nome_clean or nome_clean in desc_clean:
            resultados.append(t)
            continue
        # Substring com nome curto (sem matrícula)
        if nome_clean_short and (desc_clean in nome_clean_short or nome_clean_short in desc_clean):
            resultados.append(t)
            continue
        # Fuzzy match com threshold alto
        ratio = difflib.SequenceMatcher(None, desc_clean, nome_clean).ratio()
        if ratio >= 0.58:
            resultados.append(t)
    return resultados


def match_by_coi_keywords(codigo_coi, desc_banco, regras_fornecedores):
    """
    Fallback: verifica se a descrição do banco contém alguma palavra-chave
    do COI associado ao fornecedor. Retorna True se encontrar.
    Ex: COI 005622 (Agua) tem palavras [AGUA, SEMASA, SANEAMENTO]
        Banco: 'SERVICO MUNICIPAL DE AGUA' -> match!
    """
    if not codigo_coi:
        return False
    info_coi = regras_fornecedores.get(codigo_coi)
    if not info_coi:
        return False
    palavras = info_coi.get('palavras_chave', [])
    desc_up = clean_bank_description(desc_banco)
    for p in palavras:
        if not p:
            continue
        pattern = r'\b' + re.escape(p.upper()) + r'\b'
        if re.search(pattern, desc_up):
            return True
    return False


def is_near_end_of_month(dt, days=3):
    """Verifica se a data está nos últimos N dias do mês."""
    last_day = calendar.monthrange(dt.year, dt.month)[1]
    return dt.day >= (last_day - days)


def find_matching_value(target_valor, transacoes, tolerance=0.02):
    """
    Tenta encontrar uma ou mais transações cujo valor (ou soma) bata com o target.
    Retorna (transacoes_match, obs) ou ([], '').
    tolerance: fracao do valor (2% por padrão)
    """
    tol = max(0.05, abs(target_valor) * tolerance)  # Mínimo de 5 centavos

    # Match exato direto
    for t in transacoes:
        if abs(abs(t['valor']) - abs(target_valor)) <= tol:
            return [t], 'Match exato'

    # Sem match exato → retornar as transações candidatas para revisão manual
    return [], ''


# ------------------------------------------------------------------
# Carrega extratos da pasta extratos/ e indexa por (mes, ano)
# ------------------------------------------------------------------

def carregar_extratos(pasta='extratos'):
    """
    Lê todos os CSVs em `pasta/` e devolve um dict:
      (mes_int, ano_int) -> lista de transacoes [{data, descricao, valor, tipo}]
    """
    meses_por_chave = defaultdict(list)
    arquivos = sorted(glob.glob(os.path.join(pasta, '*.csv')))

    for arq in arquivos:
        try:
            with open(arq, 'r', encoding='latin-1', errors='replace') as f:
                reader = csv.reader(f, delimiter=';')
                for row in reader:
                    if not row or len(row) < 5:
                        continue
                    data_str = row[0].strip()
                    if not re.match(r'\d{2}/\d{2}/\d{4}', data_str):
                        continue
                    historico = row[1].strip()
                    valor_str = row[3].strip()
                    tipo = row[4].strip()
                    valor_num = parse_valor_br(valor_str)
                    if valor_num == 0:
                        continue
                    dt = parse_date(data_str)
                    if not dt:
                        continue
                    chave = (dt.month, dt.year)
                    meses_por_chave[chave].append({
                        'data': data_str,
                        'descricao': historico,
                        'valor': valor_num,
                        'tipo': tipo,
                        'data_dt': dt,
                    })
        except Exception as e:
            print(f'  [AVISO] Erro ao ler {arq}: {e}')

    print(f'Extratos indexados por mês: {sorted(meses_por_chave.keys())}')
    return meses_por_chave


# ------------------------------------------------------------------
# Motor principal por mês
# ------------------------------------------------------------------

def conciliar_mes(mes, ano, pendentes_do_mes, extratos_por_mes, mapeamento, nome_mes, regras_fornecedores):
    """
    Para um dado mês/ano:
      - pendentes_do_mes: lista de contas pendentes com vencimento naquele mês
      - extratos_por_mes: dict (mes, ano) -> lista de transações
    Devolve lista de resultados enriquecidos.
    """
    # Transações do mês principal + mês seguinte (para virada de mês)
    transacoes_mes = extratos_por_mes.get((mes, ano), [])
    mes_seguinte = mes % 12 + 1
    ano_seguinte = ano + 1 if mes == 12 else ano
    transacoes_prox = extratos_por_mes.get((mes_seguinte, ano_seguinte), [])

    resultados = []

    for conta in pendentes_do_mes:
        cod_forn = conta.get('fornecedor_codigo', '')
        nome_forn = conta.get('fornecedor_nome', '')
        valor_conta = parse_valor_br(conta.get('valor', '0'))
        venc_str = conta.get('vencimento', '')
        venc_dt = parse_date(venc_str)

        # Determinar quais transações do extrato devem ser verificadas
        transacoes_candidatas = list(transacoes_mes)
        obs_extra = ''
        if venc_dt and is_near_end_of_month(venc_dt):
            transacoes_candidatas += transacoes_prox
            obs_extra = ' [Vencimento próx. fim do mês: verificado mês seguinte também]'

        # Resolver nome e COI no sistema via mapeamento
        info_mapeamento = mapeamento.get(cod_forn, {})
        nome_sistema = info_mapeamento.get('nome_identificado', '')
        codigo_coi = info_mapeamento.get('codigo_coi', '')
        descricao_coi = info_mapeamento.get('descricao_coi', '')
        if not nome_sistema:
            nome_sistema = nome_forn

        # Buscar transações com o mesmo fornecedor (nome)
        transacoes_forn = match_supplier_by_name(nome_sistema, transacoes_candidatas) if nome_sistema else []

        # Fallback: se não achou pelo nome, tentar pelas palavras-chave do COI
        if not transacoes_forn and codigo_coi:
            transacoes_forn = [
                t for t in transacoes_candidatas
                if match_by_coi_keywords(codigo_coi, t['descricao'], regras_fornecedores)
            ]
            if transacoes_forn:
                obs_extra += ' [match por palavras-chave do COI]'

        # Tentar cruzar pelo valor
        match_transacoes, obs_valor = find_matching_value(valor_conta, transacoes_forn)

        # Definir status
        if match_transacoes:
            t = match_transacoes[0]
            resultados.append({
                'duplicata': conta.get('duplicata', ''),
                'fornecedor_cod': cod_forn,
                'fornecedor_nome': nome_forn,
                'nome_sistema': nome_sistema,
                'codigo_coi': codigo_coi,
                'descricao_coi': descricao_coi,
                'vencimento': venc_str,
                'valor_conta': valor_conta,
                'empresa': conta.get('empresa', ''),
                'status': 'ENCONTRADO NO BANCO',
                'data_banco': t['data'],
                'desc_banco': t['descricao'],
                'valor_banco': t['valor'],
                'observacao': obs_valor + obs_extra,
            })
        elif transacoes_forn:
            # Fornecedor encontrado mas valor não bate
            # Listar todas as transações candidatas como referência
            candidatos_str = ' | '.join(
                [f"{t['data']} R${t['valor']:.2f}" for t in transacoes_forn[:5]]
            )
            resultados.append({
                'duplicata': conta.get('duplicata', ''),
                'fornecedor_cod': cod_forn,
                'fornecedor_nome': nome_forn,
                'nome_sistema': nome_sistema,
                'codigo_coi': codigo_coi,
                'descricao_coi': descricao_coi,
                'vencimento': venc_str,
                'valor_conta': valor_conta,
                'empresa': conta.get('empresa', ''),
                'status': 'PARCIAL (SEM VALOR EXATO)',
                'data_banco': '',
                'desc_banco': '',
                'valor_banco': '',
                'observacao': f'Fornecedor encontrado no banco, mas valor não bate. Candidatos: {candidatos_str}{obs_extra}',
            })
        else:
            resultados.append({
                'duplicata': conta.get('duplicata', ''),
                'fornecedor_cod': cod_forn,
                'fornecedor_nome': nome_forn,
                'nome_sistema': nome_sistema,
                'codigo_coi': codigo_coi,
                'descricao_coi': descricao_coi,
                'vencimento': venc_str,
                'valor_conta': valor_conta,
                'empresa': conta.get('empresa', ''),
                'status': 'NÃO ENCONTRADO NO BANCO',
                'data_banco': '',
                'desc_banco': '',
                'valor_banco': '',
                'observacao': obs_extra.strip(),
            })

    return resultados


# ------------------------------------------------------------------
# Gera o Excel do mês
# ------------------------------------------------------------------

def _escrever_aba(wb, titulo, resultados, headers, header_fill, header_font,
                   fill_green, fill_yellow, fill_red, is_active=False):
    """Cria ou usa a aba ativa e popula com os dados fornecidos."""
    if is_active:
        ws = wb.active
        ws.title = titulo
    else:
        ws = wb.create_sheet(title=titulo)

    ws.append(headers)
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center')

    count = {'ENCONTRADO NO BANCO': 0, 'PARCIAL (SEM VALOR EXATO)': 0, 'NÃO ENCONTRADO NO BANCO': 0}

    for r in resultados:
        status = r['status']
        row_data = [
            r['duplicata'], r['fornecedor_cod'], r['fornecedor_nome'], r['nome_sistema'],
            r.get('codigo_coi', ''), r.get('descricao_coi', ''),
            r['vencimento'], r['valor_conta'], r['empresa'],
            status, r['data_banco'], r['desc_banco'],
            r['valor_banco'] if r['valor_banco'] != '' else None,
            r['observacao']
        ]
        ws.append(row_data)
        current_row = ws.max_row

        if status == 'ENCONTRADO NO BANCO':
            fill_color = fill_green
            count['ENCONTRADO NO BANCO'] += 1
        elif status == 'PARCIAL (SEM VALOR EXATO)':
            fill_color = fill_yellow
            count['PARCIAL (SEM VALOR EXATO)'] += 1
        else:
            fill_color = fill_red
            count['NÃO ENCONTRADO NO BANCO'] += 1

        for col in range(1, len(headers) + 1):
            ws.cell(row=current_row, column=col).fill = fill_color

        ws.cell(row=current_row, column=8).number_format = '#,##0.00'   # Valor SS+ (col 8 agora)
        if r['valor_banco'] != '':
            ws.cell(row=current_row, column=13).number_format = '#,##0.00'  # Valor Banco (col 13)

    # Ajustar largura
    dims = {}
    for row in ws.rows:
        for cell in row:
            if cell.value:
                dims[cell.column_letter] = max(dims.get(cell.column_letter, 0), min(len(str(cell.value)), 50))
    for col, width in dims.items():
        ws.column_dimensions[col].width = width + 2

    return count


def gerar_excel(resultados, mes_ano_str, nome_arquivo):
    wb = Workbook()

    headers = [
        'Duplicata', 'Cód. Fornecedor', 'Fornecedor (SS+)', 'Nome no Sistema',
        'Código COI', 'Desc. COI',
        'Vencimento', 'Valor SS+ (R$)', 'Empresa',
        'Status', 'Data no Banco', 'Desc. Banco', 'Valor Banco (R$)', 'Observação'
    ]

    header_fill  = PatternFill(start_color='1F3864', end_color='1F3864', fill_type='solid')
    header_font  = Font(color='FFFFFF', bold=True)
    fill_green   = PatternFill(start_color='C6EFCE', fill_type='solid')
    fill_yellow  = PatternFill(start_color='FFEB9C', fill_type='solid')
    fill_red     = PatternFill(start_color='FFC7CE', fill_type='solid')

    args = (headers, header_fill, header_font, fill_green, fill_yellow, fill_red)

    # Aba 1 — Geral (todos os resultados)
    count = _escrever_aba(wb, 'Geral', resultados, *args, is_active=True)

    # Aba 2 — Encontrados (verde)
    encontrados = [r for r in resultados if r['status'] == 'ENCONTRADO NO BANCO']
    _escrever_aba(wb, '✅ Encontrados', encontrados, *args)

    # Aba 3 — Parciais (amarelo)
    parciais = [r for r in resultados if r['status'] == 'PARCIAL (SEM VALOR EXATO)']
    _escrever_aba(wb, '🟡 Parciais', parciais, *args)

    # Aba 4 — Não Encontrados (vermelho)
    nao_enc = [r for r in resultados if r['status'] == 'NÃO ENCONTRADO NO BANCO']
    _escrever_aba(wb, '❌ Não Encontrados', nao_enc, *args)

    # Abas por Código COI
    cois_encontrados = sorted(set(r.get('codigo_coi', '') for r in resultados if r.get('codigo_coi')))
    for coi in cois_encontrados:
        resultados_coi = [r for r in resultados if r.get('codigo_coi') == coi]
        _escrever_aba(wb, f'COI {coi}', resultados_coi, *args)

    wb.save(nome_arquivo)
    return count




# ------------------------------------------------------------------
# Entrypoint principal
# ------------------------------------------------------------------

def main():
    print('=' * 60)
    print('Motor de Conciliação: Pendentes SS+ → Extrato Bancário')
    print('=' * 60)

    meses_pt = {
        1: 'Janeiro', 2: 'Fevereiro', 3: 'Marco', 4: 'Abril',
        5: 'Maio', 6: 'Junho', 7: 'Julho', 8: 'Agosto',
        9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'
    }

    # Carregar dados base
    print('\n[1/4] Carregando contas pendentes do sistema...')
    with open('contas_pagas_data.json', 'r', encoding='utf-8') as f:
        todas_contas = json.load(f)

    # Filtrar contas com vencimento válido e valor válido
    contas_validas = []
    for c in todas_contas:
        venc_dt = parse_date(c.get('vencimento', ''))
        valor = parse_valor_br(c.get('valor', '0'))
        if venc_dt and valor > 0.01:
            c['_venc_dt'] = venc_dt
            c['_valor_num'] = valor
            contas_validas.append(c)

    print(f'  -> {len(contas_validas)} contas com vencimento e valor válidos (de {len(todas_contas)} totais)')

    # Carregar mapeamento de fornecedores
    print('\n[2/5] Carregando mapeamento de fornecedores...')
    with open('mapeamento_fornecedores_coi.json', 'r', encoding='utf-8') as f:
        mapeamento = json.load(f)
    print(f'  -> {len(mapeamento)} fornecedores mapeados')

    # Carregar regras de fornecedores (palavras-chave por COI)
    print('\n[3/5] Carregando regras de palavras-chave por COI...')
    with open('regras_fornecedores_coi.json', 'r', encoding='utf-8') as f:
        regras_fornecedores = json.load(f)
    print(f'  -> {len(regras_fornecedores)} COIs com palavras-chave')

    # Carregar extratos bancários
    print('\n[4/5] Carregando extratos bancários...')
    extratos_por_mes = carregar_extratos('extratos')

    # Agrupar contas por mês de vencimento
    contas_por_mes = defaultdict(list)
    for c in contas_validas:
        dt = c['_venc_dt']
        chave = (dt.month, dt.year)
        contas_por_mes[chave].append(c)

    print(f'\n  -> Meses com pendências: {sorted(contas_por_mes.keys())}')

    # Garantir pasta de saída
    os.makedirs('relatorios_mensais', exist_ok=True)

    # Processar mês a mês
    print('\n[5/5] Conciliando mês a mês...')
    resumo_geral = []

    for (mes, ano) in sorted(contas_por_mes.keys()):
        nome_mes = meses_pt.get(mes, f'Mes{mes:02d}')
        pendentes = contas_por_mes[(mes, ano)]
        nome_arq = f'relatorios_mensais/Conciliacao_Pendentes_{nome_mes}_{ano}.xlsx'

        print(f'\n  Processando {nome_mes}/{ano}: {len(pendentes)} pendências...')

        resultados = conciliar_mes(mes, ano, pendentes, extratos_por_mes, mapeamento, nome_mes, regras_fornecedores)
        count = gerar_excel(resultados, f'{nome_mes}_{ano}', nome_arq)

        total = len(resultados)
        enc = count['ENCONTRADO NO BANCO']
        parc = count['PARCIAL (SEM VALOR EXATO)']
        nao = count['NÃO ENCONTRADO NO BANCO']

        print(f'    ✅ Encontrados: {enc} | 🟡 Parciais: {parc} | ❌ Não encontrados: {nao}')
        print(f'    -> Relatório: {nome_arq}')

        resumo_geral.append({
            'mes': f'{nome_mes}/{ano}',
            'total': total,
            'encontrados': enc,
            'parciais': parc,
            'nao_encontrados': nao,
        })

    # Resumo Final
    print('\n' + '=' * 60)
    print('RESUMO GERAL')
    print('=' * 60)
    total_enc = sum(r['encontrados'] for r in resumo_geral)
    total_parc = sum(r['parciais'] for r in resumo_geral)
    total_nao = sum(r['nao_encontrados'] for r in resumo_geral)
    total_all = sum(r['total'] for r in resumo_geral)
    print(f'Total de pendências analisadas : {total_all}')
    print(f'✅ Encontradas no banco        : {total_enc} ({100*total_enc//total_all if total_all else 0}%)')
    print(f'🟡 Parciais (verificar)        : {total_parc} ({100*total_parc//total_all if total_all else 0}%)')
    print(f'❌ Não encontradas             : {total_nao} ({100*total_nao//total_all if total_all else 0}%)')
    print('\nProcessamento finalizado!')


if __name__ == '__main__':
    main()
