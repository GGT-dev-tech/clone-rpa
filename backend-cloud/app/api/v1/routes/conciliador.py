import os
import tempfile
from pathlib import Path
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status

from app.schemas.task import TaskCreate, TaskType, BaixaContasPagarPayload
from app.services.task_service import TaskService
from app.services.coi_engine_service import COIEngineService

router = APIRouter()

# Assumindo que os scripts de engine estão dentro de app/services/coi_engine/
ENGINE_DIR = Path(__file__).parent.parent.parent.parent / "services" / "coi_engine"

def get_coi_engine() -> COIEngineService:
    return COIEngineService(engine_dir=ENGINE_DIR)

@router.post("/processar")
async def processar_arquivos(
    pendentes_file: UploadFile = File(...),
    extrato_file: UploadFile = File(...),
    task_service: TaskService = Depends(TaskService),
    coi_engine: COIEngineService = Depends(get_coi_engine)
):
    """
    Recebe o JSON de pendentes e o CSV do extrato bancário.
    Executa a conciliação COI e cria as tarefas de BAIXA_CONTAS_PAGAR para o RPA.
    """
    if not pendentes_file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Arquivo pendentes deve ser .json")
    if not extrato_file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Arquivo extrato deve ser .csv")

    with tempfile.TemporaryDirectory() as temp_dir:
        pendentes_path = Path(temp_dir) / pendentes_file.filename
        extrato_path = Path(temp_dir) / extrato_file.filename
        
        with open(pendentes_path, "wb") as f:
            f.write(await pendentes_file.read())
            
        with open(extrato_path, "wb") as f:
            f.write(await extrato_file.read())

        try:
            # Roda o motor de inteligência
            baixas = coi_engine.conciliar_e_obter_baixas(pendentes_path, extrato_path)
            
            # Para cada baixa identificada, enfileira a task
            tasks_criadas = []
            for b in baixas:
                payload = BaixaContasPagarPayload(**b)
                task_in = TaskCreate(
                    task_type=TaskType.BAIXA_CONTAS_PAGAR,
                    payload=payload
                )
                
                # Gera idempotency_key simples pelo payload
                import hashlib
                idempotency_key = hashlib.sha256(
                    task_in.model_dump_json(sort_keys=True).encode()
                ).hexdigest()
                
                task = await task_service.create_and_enqueue(task_in, idempotency_key)
                tasks_criadas.append(task)

            return {
                "message": f"Processamento concluído. {len(tasks_criadas)} tarefas geradas.",
                "tasks": tasks_criadas
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
