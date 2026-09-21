import json
import csv
import re
from pathlib import Path
from decimal import Decimal
from typing import Any, Dict, List, Tuple
import traceback
import logging

from app.services.coi_engine.conciliador_extrato_coi import (
    parse_valor_br, match_supplier, match_bank_rule,
    sugerir_codigo_coi, find_matching_duplicatas
)

logger = logging.getLogger(__name__)

class COIEngineService:
    def __init__(self, engine_dir: Path):
        self.engine_dir = engine_dir
        self.mapeamento = self._load_json("mapeamento_fornecedores_coi.json")
        self.regras_bancarias = self._load_json("regras_bancarias_coi.json")
        self.regras_fornecedores = self._load_json("regras_fornecedores_coi.json")
        self.coi_data = {item["codigo"]: item for item in self._load_json("coi_data.json")}

    def _load_json(self, filename: str) -> Any:
        file_path = self.engine_dir / filename
        if not file_path.exists():
            return {} if not filename == "coi_data.json" else []
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def conciliar_e_obter_baixas(self, pendentes_json_path: Path, extrato_csv_path: Path) -> List[Dict[str, Any]]:
        """
        Roda a conciliação e retorna os itens exatos prontos para baixar via RPA,
        já com o codigo_coi mapeado.
        """
        logger.info(f"Iniciando conciliação de {extrato_csv_path} contra {pendentes_json_path}")
        
        with open(pendentes_json_path, "r", encoding="utf-8") as f:
            contas_pendentes = json.load(f)

        baixas_prontas = []
        try:
            with open(extrato_csv_path, 'r', encoding='latin-1', errors='replace') as f:
                reader = csv.reader(f, delimiter=';')
                for row in reader:
                    if not row or len(row) < 5: continue
                    data = row[0].strip()
                    if not re.match(r'\d{2}/\d{2}/\d{4}', data):
                        continue
                    
                    historico = row[1].strip()
                    doc = row[2].strip()
                    valor_str = row[3].strip()
                    tipo = row[4].strip()
                    valor_num = parse_valor_br(valor_str)
                    
                    if valor_num == 0 or tipo != 'D': continue
                    
                    cod_coi, desc_coi, regra_aplicada, conf = self._descobrir_coi(historico)
                    
                    if not cod_coi:
                        continue

                    duplicatas_candidatas = contas_pendentes.get(data, [])
                    if not duplicatas_candidatas:
                        continue
                        
                    matches = find_matching_duplicatas(valor_num, duplicatas_candidatas, tolerance=0.01)
                    if len(matches) == 1:
                        match = matches[0]
                        contas_pendentes[data].remove(match)
                        
                        duplicata_id = str(match.get('duplicata', ''))
                        if duplicata_id:
                            d, m, y = data.split('/')
                            dt_iso = f"{y}-{m}-{d}"
                            
                            baixas_prontas.append({
                                "duplicata_id": duplicata_id,
                                "data_pagamento": dt_iso,
                                "valor": abs(float(match.get('valor_num', valor_num))),
                                "conta": "001",
                                "observacao": f"Auto COI: {desc_coi} (Conciliado)",
                                "codigo_coi": cod_coi
                            })

        except Exception as e:
            logger.error(f"Erro na conciliação: {e}")
            traceback.print_exc()

        return baixas_prontas

    def _descobrir_coi(self, historico: str) -> Tuple[str, str, str, str]:
        r_bancaria = match_bank_rule(historico, self.regras_bancarias)
        if r_bancaria:
            return r_bancaria['codigo_coi'], r_bancaria['descricao_coi'], r_bancaria.get('tipo_regra', 'regra_bancaria'), 'Alta'
            
        r_fornecedor = match_supplier(historico, self.mapeamento)
        if r_fornecedor:
            return r_fornecedor['codigo_coi'], r_fornecedor['descricao_coi'], r_fornecedor.get('tipo_regra', 'fornecedor'), 'Media'
            
        cod, desc = sugerir_codigo_coi(historico, self.regras_fornecedores)
        if cod:
            return cod, desc, 'palavra_chave', 'Baixa'
            
        return "", "", "", ""
