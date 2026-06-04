"""
VibeMask Web API Backend.
Serves as the local backend for the Tauri/React UI.
"""

import shutil
import os
from pathlib import Path
from typing import List, Dict
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..core import factory
from ..detector.hybrid import HybridDetector
from ..masker.placeholder import PlaceholderGenerator
from ..core.replacer import apply_processor_replacements
from ..vault.storage import VaultStorage
from ..core.span import EntityType

app = FastAPI(title="VibeMask API")

# Allow CORS for local UI development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Temp directory for uploads
UPLOAD_DIR = Path.cwd() / "temp_uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

class AnalysisResult(BaseModel):
    original: str
    type: str
    masked: str
    count: int

class MaskResponse(BaseModel):
    file_path: str
    session_id: str
    stats: Dict[str, int]
    preview: List[AnalysisResult]

def cleanup_file(path: Path):
    """Delete temp file after response."""
    if path.exists():
        os.remove(path)

@app.post("/analyze")
async def analyze_file(
    file: UploadFile = File(...),
    language: str = Form("zh")
):
    """Upload and analyze a file without masking."""
    temp_path = UPLOAD_DIR / file.filename
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # factory load
        processor = factory.get_processor(temp_path)
        processor.load()
        processor.extract_segments()
        text = processor.get_combined_text()
        
        # detect
        spans = HybridDetector().detect_document(processor, text)
        
        # summarize
        generator = PlaceholderGenerator()
        summary = {}
        for span in spans:
            entity_type = span.type.value
            if span.text not in summary:
                try:
                    etype = EntityType(entity_type)
                except ValueError:
                    etype = EntityType.UNKNOWN
                    
                summary[span.text] = {
                    "type": entity_type,
                    "masked": generator.generate(span.text, etype),
                    "count": 0
                }
            summary[span.text]["count"] += 1
            
        return [
            AnalysisResult(
                original=k,
                type=v["type"],
                masked=v["masked"],
                count=v["count"]
            )
            for k, v in summary.items()
        ]
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cleanup_file(temp_path)

@app.post("/mask")
async def mask_file(
    file: UploadFile = File(...),
    language: str = Form("zh")
):
    """Mask a file and return session ID."""
    temp_path = UPLOAD_DIR / file.filename
    masked_path = UPLOAD_DIR / f"{Path(file.filename).stem}_masked{Path(file.filename).suffix}"
    
    # Handle legacy extension change
    if Path(file.filename).suffix.lower() == '.doc':
        masked_path = masked_path.with_suffix('.docx')
    elif Path(file.filename).suffix.lower() == '.xls':
        masked_path = masked_path.with_suffix('.xlsx')
    
    try:
        # Save upload
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Process
        processor = factory.get_processor(temp_path)
        processor.load()
        processor.extract_segments()
        text = processor.get_combined_text()
        
        # Detect
        spans = HybridDetector().detect_document(processor, text)
        
        # Prepare replacements
        vault = VaultStorage(str(Path.cwd()))
        generator = PlaceholderGenerator()
        replacements = {}
        stats = {}
        
        for span in spans:
            original = span.text
            entity_type = span.type.value
            stats[entity_type] = stats.get(entity_type, 0) + 1
            
            if original in replacements:
                continue
                
            try:
                etype = EntityType(entity_type)
            except ValueError:
                etype = EntityType.UNKNOWN
            
            proposed = generator.generate(original, etype)
            final = vault.get_or_create_mapping(
                original, entity_type, proposed,
                source=span.source.value, confidence=span.confidence
            )
            replacements[original] = final
            
        # Apply
        apply_processor_replacements(processor, replacements, spans)
        processor.save(masked_path)
        
        # Save Session
        reverse_map = {v: k for k, v in replacements.items()}
        # Use simple filename for local session tracking
        session_id = vault.create_session(
            input_files=[file.filename],
            mappings=reverse_map,
            stats=stats
        )
        
        # Preview data
        preview = [
            AnalysisResult(
                original=k,
                type=next((s.type.value for s in spans if s.text == k), "?"),
                masked=v,
                count=sum(1 for s in spans if s.text == k)
            ) 
            for k, v in list(replacements.items())[:50]
        ]
        
        return MaskResponse(
            file_path=str(masked_path.name), # Just filename for download
            session_id=session_id,
            stats=stats,
            preview=preview
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/{filename}")
async def download_file(filename: str, background_tasks: BackgroundTasks):
    """Download processed file."""
    path = UPLOAD_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
        
    # Schedule cleanup after download? Only if temp. 
    # For now let's keep it until restart to allow retries.
    return FileResponse(
        path, 
        filename=filename,
        media_type='application/octet-stream'
    )

@app.post("/restore")
async def restore_file(
    file: UploadFile = File(...)
):
    """Restore a masked file."""
    temp_path = UPLOAD_DIR / file.filename
    restored_path = UPLOAD_DIR / f"restored_{file.filename}"
    
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Auto-detect session
        vault = VaultStorage(str(Path.cwd()))
        
        # Find session
        # We need a proper search method in Vault. 
        # For now, let's look at recent sessions
        sessions = vault.list_sessions(limit=20)
        best_session = None
        
        # Read file snippet for content matching
        processor = factory.get_processor(temp_path)
        processor.load()
        processor.extract_segments()
        text_sample = processor.get_combined_text()[:2000]
        
        max_matches = 0
        for s in sessions:
            matches = sum(1 for masked in s.mappings.keys() if masked in text_sample)
            if matches > max_matches:
                max_matches = matches
                best_session = s
                
        if not best_session or max_matches == 0:
            raise HTTPException(status_code=400, detail="No matching session found for this file.")
            
        # Restore
        mappings = best_session.mappings
        processor.replace_text(mappings)
        processor.save(restored_path)
        
        return {
            "file_path": restored_path.name,
            "session_id": best_session.session_id,
            "restored_count": max_matches
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
