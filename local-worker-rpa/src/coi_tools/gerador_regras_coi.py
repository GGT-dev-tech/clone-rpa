import json
import re

# Stopwords genéricas que aparecem nas descrições COI mas não são boas chaves
STOPWORDS = {"PAG", "DE", "C", "COM", "DA", "DO", "E", "EM", "PARA", "POR", "DOS", "DAS", "PAGAMENTO", "DESPESAS", "DESPESA", "OUTROS", "RECEBIMENTO"}

def extrair_palavras_chave(descricao):
    # Remove acentos básicos (gambiarra rápida sem unidecode)
    desc = descricao.upper()
    replacements = {'Á':'A', 'É':'E', 'Í':'I', 'Ó':'O', 'Ú':'U', 'Ã':'A', 'Õ':'O', 'Ç':'C', 'Â':'A', 'Ê':'E'}
    for a, b in replacements.items():
        desc = desc.replace(a, b)
        
    # Extrai apenas palavras
    palavras = re.findall(r'[A-Z]+', desc)
    
    # Filtra
    chaves = []
    for p in palavras:
        if len(p) > 2 and p not in STOPWORDS:
            chaves.append(p)
    return list(set(chaves))

def main():
    print("Carregando coi_data.json...")
    with open('coi_data.json', 'r', encoding='utf-8') as f:
        coi_list = json.load(f)
        
    regras = {}
    
    # Pré-carregar algumas associações inteligentes
    manual_overrides = {
        "005118": ["ENERGIA", "CELESC", "LUZ", "ELETRICA", "MANUTENCAO PREDIAL"],
        "005622": ["AGUA", "SEMASA", "SANEAMENTO"],
        "005134": ["ISS ", "PREFEITURA", "MUNICIPIO", "IMPOSTO MUNICIPAL"],
        "004685": ["HONORARIOS", "CONTABEIS", "CONTABILIDADE"],
        "005126": ["COMBUSTIVEL", "POSTO", "GASOLINA", "DIESEL", "ETANOL"],
        "004881": ["ALIMENTACAO", "REFEICAO", "MERCADO"],
        "004863": ["FRETE", "TRANSPORTES", "LOGISTICA"],
        "005924": ["PEDAGIO", "CONCESSIONARIA"],
        "004944": ["SERVICOS", "MANUTENCAO"],
        "004847": ["FUNDO DE COMERCIO", "ALUGUEL"],
        "004650": ["SOFTWARE", "SISTEMA", "MENSALIDADE"]
    }
    
    for coi in coi_list:
        cod = coi['codigo']
        desc = coi['descricao']
        
        # Gera palavras base
        chaves = extrair_palavras_chave(desc)
        
        # Sobrescreve com as manuais se existir
        if cod in manual_overrides:
            chaves = manual_overrides[cod]
            
        regras[cod] = {
            "descricao_coi": desc,
            "palavras_chave": chaves
        }
        
    with open('regras_fornecedores_coi.json', 'w', encoding='utf-8') as f:
        json.dump(regras, f, indent=2, ensure_ascii=False)
        
    print(f"Gerado regras_fornecedores_coi.json com {len(regras)} códigos COI configuráveis.")

if __name__ == "__main__":
    main()
