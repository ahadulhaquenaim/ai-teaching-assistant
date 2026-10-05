"""Document endpoints: upload (async ingestion), list, get (status polling), delete."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile, status

from app.core.dependencies import CurrentUserDep, get_document_service, get_ingestion_service
from app.db.repositories.documents import to_document_out
from app.schemas.document import DocumentListResponse, DocumentOut, DocumentUploadResponse
from app.services.documents import DocumentService
from app.services.ingestion import IngestionService

router = APIRouter(prefix="/documents", tags=["documents"])

DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=DocumentUploadResponse)
async def upload_document(
    user: CurrentUserDep,
    service: DocumentServiceDep,
    ingestion: Annotated[IngestionService, Depends(get_ingestion_service)],
    background: BackgroundTasks,
    file: Annotated[UploadFile, File(description="PDF or DOCX file")],
) -> DocumentUploadResponse:
    """Validate and accept an upload; processing continues in the background.

    Poll `GET /documents/{id}` until `status` is `ready` or `failed`.
    """
    upload = await service.validate_upload(file)
    document_id = await service.create(user.user_id, upload)
    background.add_task(ingestion.run, document_id, upload.filename, upload.file_type, upload.data)
    return DocumentUploadResponse(id=document_id)


@router.get("", response_model=DocumentListResponse)
async def list_documents(user: CurrentUserDep, service: DocumentServiceDep) -> DocumentListResponse:
    docs = await service.list(user.user_id)
    return DocumentListResponse(documents=[to_document_out(d) for d in docs])


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: str, user: CurrentUserDep, service: DocumentServiceDep
) -> DocumentOut:
    return to_document_out(await service.get(user.user_id, document_id))


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str, user: CurrentUserDep, service: DocumentServiceDep
) -> None:
    """Delete the document, its Pinecone namespace, and all related chats/quizzes."""
    await service.delete(user.user_id, document_id)
