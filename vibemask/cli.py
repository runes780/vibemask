"""
VibeMask CLI - Command line interface.
"""

import typer
import click
from pathlib import Path
from typing import Optional, List
from rich.console import Console
from rich.table import Table
from rich.panel import Panel


class VibeMaskGroup(typer.core.TyperGroup):
    """Route unknown top-level commands to the generic command wrapper."""

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            if args and args[0] not in self.commands:
                command = self.commands.get("exec")
                if command is not None:
                    return "exec", command, args
            raise


app = typer.Typer(
    name="vibemask",
    cls=VibeMaskGroup,
    help="🎭 Privacy-preserving AI tool wrapper with automatic masking and restoration",
    add_completion=False,
)

console = Console()

LEGACY_OFFICE_EXTENSIONS = {".doc", ".xls", ".ppt"}
LEGACY_TO_OOXML_EXTENSION = {
    ".doc": ".docx",
    ".xls": ".xlsx",
    ".ppt": ".pptx",
}


def get_project_path(project_root: Optional[Path] = None, file_path: Optional[Path] = None) -> Path:
    """Resolve the VibeMask project root used for vault isolation."""
    if project_root is not None and project_root.__class__.__name__ == "OptionInfo":
        project_root = None
    if file_path is not None and file_path.__class__.__name__ == "OptionInfo":
        file_path = None
    if project_root is not None:
        return Path(project_root).expanduser().resolve()
    if file_path is not None:
        return Path(file_path).expanduser().resolve().parent
    return Path.cwd().resolve()


@app.command()
def mask(
    file: Path = typer.Argument(..., help="File to mask"),
    output: Optional[Path] = typer.Option(None, "-o", "--output", help="Output file"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without writing"),
    interactive: bool = typer.Option(False, "-i", "--interactive", help="Confirm detection"),
    language: str = typer.Option("zh", "-l", "--language", help="Main language (zh, en, ja, es)"),
    engine: str = typer.Option(
        "hybrid",
        "--engine",
        help="Detection engine: hybrid | privacy-filter-mlx | privacy-filter | regex | qwen | presidio",
    ),
    use_ollama: bool = typer.Option(False, "--ollama", help="Use Ollama for verification"),
    ollama_model: str = typer.Option("qwen2.5:0.5b", "--model", help="Ollama model to use"),
    qwen_url: str = typer.Option("http://localhost:11434", "--qwen-url", help="Ollama server URL"),
    qwen_model: str = typer.Option("qwen3:4b-instruct", "--qwen-model", help="Qwen model name"),
    qwen_stream: bool = typer.Option(False, "--qwen-stream", help="Use streaming generation"),
    qwen_timeout: int = typer.Option(180, "--qwen-timeout", help="Qwen request timeout (seconds)"),
    qwen_chunk_chars: int = typer.Option(1800, "--qwen-chunk-chars", help="Chunk size for long texts"),
    privacy_device: str = typer.Option(
        "cpu",
        "--privacy-device",
        help="Privacy Filter device: cpu | cuda",
    ),
    privacy_checkpoint: Optional[Path] = typer.Option(
        None,
        "--privacy-checkpoint",
        help="Privacy Filter checkpoint directory",
    ),
    privacy_context_window: Optional[int] = typer.Option(
        None,
        "--privacy-context-window",
        help="Privacy Filter context window length",
    ),
    privacy_decode_mode: str = typer.Option(
        "viterbi",
        "--privacy-decode-mode",
        help="Privacy Filter decode mode: viterbi | argmax",
    ),
    privacy_backend: str = typer.Option(
        "mlx",
        "--privacy-backend",
        help="Privacy Filter backend for hybrid: mlx | opf",
    ),
    project_root: Optional[Path] = typer.Option(
        None,
        "--project-root",
        help="Vault project root. Defaults to the input file's folder.",
    ),
    convert_legacy: bool = typer.Option(
        True,
        "--convert-legacy/--no-convert-legacy",
        help="Convert .doc/.xls/.ppt to OOXML via LibreOffice for lossless processing",
    ),
    keep_legacy: bool = typer.Option(
        False,
        "--keep-legacy/--no-keep-legacy",
        help="Convert output back to legacy format (.doc/.xls/.ppt). Warning: may lose formatting",
    ),
    soffice: Optional[Path] = typer.Option(
        None,
        "--soffice",
        help="Path to LibreOffice 'soffice' binary (optional)",
    ),
    allow_lossy: bool = typer.Option(
        False,
        "--allow-lossy/--no-allow-lossy",
        help="If LibreOffice is unavailable, fall back to lossy legacy processing when possible",
    ),
):
    """
    🔒 Mask sensitive information in a file.
    
    Supports: txt, md, json, yaml, csv, doc, docx, xls, xlsx, ppt, pptx, pdf

    Note: For legacy Office formats (.doc/.xls/.ppt), VibeMask will convert via LibreOffice
    to OOXML for lossless processing by default.
    """
    from .core import factory
    from .masker.placeholder import PlaceholderGenerator
    from .vault.storage import VaultStorage
    from .core.span import EntityType
    from .core.converter import convert_to_ooxml, convert_back_to_legacy
    import tempfile
    from contextlib import ExitStack

    def _option_default(value):
        return value.default if value.__class__.__name__ == "OptionInfo" else value

    dry_run = _option_default(dry_run)
    interactive = _option_default(interactive)
    language = _option_default(language)
    engine = _option_default(engine)
    use_ollama = _option_default(use_ollama)
    ollama_model = _option_default(ollama_model)
    qwen_url = _option_default(qwen_url)
    qwen_model = _option_default(qwen_model)
    qwen_stream = _option_default(qwen_stream)
    qwen_timeout = _option_default(qwen_timeout)
    qwen_chunk_chars = _option_default(qwen_chunk_chars)
    privacy_device = _option_default(privacy_device)
    privacy_checkpoint = _option_default(privacy_checkpoint)
    privacy_context_window = _option_default(privacy_context_window)
    privacy_decode_mode = _option_default(privacy_decode_mode)
    privacy_backend = _option_default(privacy_backend)
    project_root = _option_default(project_root)
    convert_legacy = _option_default(convert_legacy)
    keep_legacy = _option_default(keep_legacy)
    soffice = _option_default(soffice)
    allow_lossy = _option_default(allow_lossy)
    
    if not file.exists():
        console.print(f"[red]Error:[/red] File not found: {file}")
        raise typer.Exit(1)

    original_file = file
    processing_file = file
    original_ext = original_file.suffix.lower()
    resolved_project_path = get_project_path(project_root, original_file)
        
    # 1. Initialize Processor
    with ExitStack() as stack:
        if convert_legacy and original_ext in LEGACY_OFFICE_EXTENSIONS:
            try:
                temp_dir = stack.enter_context(tempfile.TemporaryDirectory(prefix="vibemask_"))
                processing_file = convert_to_ooxml(
                    original_file,
                    output_dir=Path(temp_dir),
                    soffice_path=str(soffice) if soffice else None,
                )
                console.print(
                    f"[dim]Converted legacy file to {processing_file.name} for lossless processing[/dim]"
                )
            except Exception as e:
                if allow_lossy:
                    console.print(
                        "[yellow]Warning:[/yellow] LibreOffice conversion failed; "
                        "falling back to lossy legacy processing."
                    )
                    console.print(f"[dim]{e}[/dim]")
                    processing_file = original_file
                else:
                    console.print(
                        f"[red]Error:[/red] LibreOffice conversion required for {original_ext} "
                        "to preserve formatting."
                    )
                    console.print(f"[dim]{e}[/dim]")
                    console.print(
                        "[dim]Install LibreOffice or pass --soffice, or use --allow-lossy/--no-convert-legacy.[/dim]"
                    )
                    raise typer.Exit(1)

        try:
            processor = factory.get_processor(processing_file)
            processor.load()
            processor.extract_segments()
        except Exception as e:
            console.print(f"[red]Error loading file:[/red] {e}")
            console.print(f"[dim]Supported formats: {factory.get_supported_formats()}[/dim]")
            raise typer.Exit(1)
        
        console.print(
            f"[cyan]📄 Processing:[/cyan] {original_file.name} "
            f"[dim]({processor.__class__.__name__})[/dim]"
        )
        console.print(f"[dim]  Project: {resolved_project_path}[/dim]")
    
        # 2. Extract Text & Detect
        text = processor.get_combined_text()
        if not text:
            console.print("[yellow]No text extracted from file.[/yellow]")
            return
        
        console.print(f"[dim]  Analyzing {len(text)} characters...[/dim]")
    
        engine = engine.strip().lower()
        privacy_backend = privacy_backend.strip().lower()

        results = []
        span_results = []
        if engine == "hybrid":
            from .detector import hybrid

            if privacy_backend not in {"opf", "mlx"}:
                console.print(
                    f"[red]Error:[/red] Unknown privacy backend: {privacy_backend}. Use opf | mlx."
                )
                raise typer.Exit(1)

            detector = hybrid.HybridDetector(
                privacy_device=privacy_device,
                privacy_checkpoint=privacy_checkpoint,
                privacy_context_window=privacy_context_window,
                privacy_decode_mode=privacy_decode_mode,
                privacy_backend=privacy_backend,
            )
            spans = detector.detect_document(processor, text)
            span_results = spans
            for span in spans:
                results.append(
                    type(
                        "Tmp",
                        (),
                        {
                            "text": span.text,
                            "entity_type": span.type.value,
                            "score": span.confidence,
                            "source": span.source.value,
                        },
                    )()
                )
        elif engine in {"privacy-filter", "privacy_filter"}:
            from .core.merger import merge_spans
            from .detector import privacy_filter

            detector = privacy_filter.PrivacyFilterDetector(
                device=privacy_device,
                checkpoint=privacy_checkpoint,
                context_window_length=privacy_context_window,
                decode_mode=privacy_decode_mode,
            )
            spans = merge_spans(detector.detect(text), text)
            span_results = spans
            for span in spans:
                results.append(
                    type(
                        "Tmp",
                        (),
                        {
                            "text": span.text,
                            "entity_type": span.type.value,
                            "score": span.confidence,
                            "source": span.source.value,
                        },
                    )()
                )
        elif engine in {"privacy-filter-mlx", "privacy_filter_mlx"}:
            from .core.merger import merge_spans
            from .detector import privacy_filter_mlx

            detector = privacy_filter_mlx.PrivacyFilterMLXDetector(
                checkpoint=privacy_checkpoint,
            )
            spans = merge_spans(detector.detect(text), text)
            span_results = spans
            for span in spans:
                results.append(
                    type(
                        "Tmp",
                        (),
                        {
                            "text": span.text,
                            "entity_type": span.type.value,
                            "score": span.confidence,
                            "source": span.source.value,
                        },
                    )()
                )
        elif engine == "regex":
            from .core.merger import merge_spans
            from .detector.regex import detect_by_regex

            spans = merge_spans(detect_by_regex(text), text)
            span_results = spans
            for span in spans:
                results.append(
                    type(
                        "Tmp",
                        (),
                        {
                            "text": span.text,
                            "entity_type": span.type.value,
                            "score": span.confidence,
                            "source": span.source.value,
                        },
                    )()
                )
        elif engine == "qwen":
            from .detector.llm_scanner import LLMScanner

            def _normalize_qwen_type(t: str) -> str:
                t = (t or "").strip().upper()
                if t in {"ID", "CN_ID_CARD", "ID_CARD"}:
                    return "IDCN"
                if t in {"PHONE_NUMBER"}:
                    return "PHONE"
                if t in {"EMAIL_ADDRESS"}:
                    return "EMAIL"
                if t in {"DATE"}:
                    return "DATE_TIME"
                if t in {"LOCATION"}:
                    return "ADDRESS"
                return t or "UNKNOWN"

            scanner = LLMScanner(
                base_url=qwen_url,
                model=qwen_model,
                timeout=float(qwen_timeout),
                max_chunk_chars=qwen_chunk_chars,
            )
            llm_result = scanner.scan_and_rewrite(text, stream=qwen_stream)

            # Convert to pseudo results compatible with existing preview pipeline.
            # We only have unique strings + types; compute approximate counts via substring occurrences.
            for original, pii_type in llm_result.pii_types.items():
                results.append(
                    type(
                        "Tmp",
                        (),
                        {
                            "text": original,
                            "entity_type": _normalize_qwen_type(pii_type),
                            "score": 1.0,
                            "source": "qwen",
                        },
                    )()
                )
        elif engine == "presidio":
            from .detector.presidio_engine import VibeMaskPresidioEngine

            presidio = VibeMaskPresidioEngine(
                use_ollama=use_ollama,
                ollama_model=ollama_model,
            )
            results = presidio.analyze(text, language=language)
        else:
            console.print(
                f"[red]Error:[/red] Unknown engine: {engine}. "
                "Use hybrid | privacy-filter-mlx | privacy-filter | regex | qwen | presidio."
            )
            raise typer.Exit(1)

        if not results:
            console.print("[yellow]No sensitive information detected.[/yellow]")
            return
        
        # 3. Generate Mappings (using Vault for consistency)
        vault = VaultStorage(str(resolved_project_path))
        generator = PlaceholderGenerator()
        
        replacements = {}
        mapping_candidates = {}
        stats = {}
        seen_entities = set()
        
        for res in results:
            original = res.text
            entity_type = res.entity_type

            # Approx count: qwen returns unique entities; span-based engines return one result per span.
            occurrences = text.count(original) if engine == "qwen" else 1
            stats[entity_type] = stats.get(entity_type, 0) + occurrences
            
            if original in seen_entities:
                continue
            seen_entities.add(original)
            
            # Determine strict EntityType if possible, or string fallback
            try:
                etype_enum = EntityType(entity_type)
            except ValueError:
                # Fallback for unknown types (e.g. from spaCy custom)
                etype_enum = EntityType.UNKNOWN
                
            proposed_mask = generator.generate(original, etype_enum)
            
            mapping_candidates[original] = (
                entity_type,
                proposed_mask,
                res.source,
                res.score,
            )
            replacements[original] = proposed_mask
        
        # 4. Preview
        table = Table(title=f"🔍 Detected in {original_file.name}")
        table.add_column("Type", style="cyan")
        table.add_column("Original", style="red")
        table.add_column("Masked", style="green")
        table.add_column("Count", justify="right")
        
        shown = 0
        for original, masked in list(replacements.items())[:20]:
            # Count occurrences in results
            if engine == "qwen":
                count = text.count(original)
            else:
                count = sum(1 for r in results if r.text == original)
            etype = next((r.entity_type for r in results if r.text == original), "?")
            table.add_row(etype, original, masked, str(count))
            shown += 1
            
        if len(replacements) > 20:
            table.add_row("...", "...", "...", "...")
            
        console.print(table)
        console.print(f"[dim]Total unique entities: {len(replacements)}[/dim]")
        
        if dry_run:
            return

        if interactive:
            if not typer.confirm("Proceed with masking?"):
                raise typer.Exit(0)

        stack.enter_context(vault.batch("mask-session"))
        for original, candidate in mapping_candidates.items():
            entity_type, proposed_mask, source, confidence = candidate
            replacements[original] = vault.get_or_create_mapping(
                original=original,
                entity_type=entity_type,
                masked=proposed_mask,
                source=source,
                confidence=confidence,
            )
            
        # 5. Apply Replacements
        try:
            from .core.replacer import apply_processor_replacements

            count = apply_processor_replacements(processor, replacements, span_results)
            
            # Determine output path
            output_provided = output is not None
            final_output = output if output_provided else original_file.with_stem(original_file.stem + "_masked")
            final_output_ext = final_output.suffix.lower()

            want_legacy_output = False
            if output_provided and final_output_ext in LEGACY_OFFICE_EXTENSIONS:
                want_legacy_output = True
            elif (not output_provided) and keep_legacy and original_ext in LEGACY_OFFICE_EXTENSIONS:
                want_legacy_output = True

            if want_legacy_output:
                if original_ext in LEGACY_OFFICE_EXTENSIONS:
                    legacy_target = original_ext[1:]
                else:
                    legacy_target = final_output_ext[1:]

                if final_output_ext not in LEGACY_OFFICE_EXTENSIONS:
                    # Ensure suffix matches requested legacy type.
                    final_output = final_output.with_suffix(f".{legacy_target}")

                with tempfile.TemporaryDirectory(prefix="vibemask_out_") as out_tmp:
                    ooxml_ext = LEGACY_TO_OOXML_EXTENSION.get(f".{legacy_target}", processor.file_path.suffix)
                    ooxml_path = Path(out_tmp) / f"{final_output.stem}{ooxml_ext}"
                    processor.save(ooxml_path)
                    saved_path = convert_back_to_legacy(
                        ooxml_path,
                        legacy_target,
                        output_path=final_output,
                        soffice_path=str(soffice) if soffice else None,
                    )
            else:
                # Default: preserve OOXML when starting from legacy.
                if original_ext in LEGACY_OFFICE_EXTENSIONS and final_output_ext in LEGACY_OFFICE_EXTENSIONS:
                    final_output = final_output.with_suffix(LEGACY_TO_OOXML_EXTENSION[original_ext])

                saved_path = processor.save(final_output)

            console.print(f"[green]✓[/green] Masked file saved: {saved_path}")
            console.print(f"[dim]Replaced {count} occurrences[/dim]")
            
            # 6. Save Session
            # Create mappings dict for session (masked -> original)
            reverse_mappings = {v: k for k, v in replacements.items()}
            
            session_id = vault.create_session(
                input_files=[str(original_file.absolute())],
                output_files=[str(saved_path.absolute())],
                mappings=reverse_mappings,
                stats=stats
            )
            console.print(f"[dim]Session ID: {session_id}[/dim]")
            
        except Exception as e:
            console.print(f"[red]Error applying masking:[/red] {e}")
            import traceback
            console.print(traceback.format_exc())
            raise typer.Exit(1)


@app.command()
def restore(
    file: Path = typer.Argument(..., help="File to restore (e.g. file_masked.docx)"),
    session_id: Optional[str] = typer.Option(None, "-s", "--session", help="Session ID (optional)"),
    output: Optional[Path] = typer.Option(None, "-o", "--output", help="Output file"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without writing"),
    project_root: Optional[Path] = typer.Option(
        None,
        "--project-root",
        help="Vault project root. Defaults to the input file's folder.",
    ),
    convert_legacy: bool = typer.Option(
        True,
        "--convert-legacy/--no-convert-legacy",
        help="Convert .doc/.xls/.ppt to OOXML via LibreOffice for lossless restoration",
    ),
    keep_legacy: bool = typer.Option(
        False,
        "--keep-legacy/--no-keep-legacy",
        help="Convert output back to legacy format (.doc/.xls/.ppt). Warning: may lose formatting",
    ),
    soffice: Optional[Path] = typer.Option(
        None,
        "--soffice",
        help="Path to LibreOffice 'soffice' binary (optional)",
    ),
    allow_lossy: bool = typer.Option(
        False,
        "--allow-lossy/--no-allow-lossy",
        help="If LibreOffice is unavailable, fall back to lossy legacy restoration when possible",
    ),
):
    """
    🔓 Restore masked content to original.
    
    Automatically finds the latest session for the file if Session ID is not provided.

    Note: For legacy Office formats (.doc/.xls/.ppt), VibeMask will convert via LibreOffice
    to OOXML for lossless restoration by default.
    """
    from .core import factory
    from .vault.storage import VaultStorage
    from .core.converter import convert_to_ooxml, convert_back_to_legacy
    import tempfile
    from contextlib import ExitStack

    def _option_default(value):
        return value.default if value.__class__.__name__ == "OptionInfo" else value

    project_root = _option_default(project_root)
    
    if not file.exists():
        console.print(f"[red]Error:[/red] File not found: {file}")
        raise typer.Exit(1)

    original_file = file
    original_ext = original_file.suffix.lower()
    resolved_project_path = get_project_path(project_root, original_file)

    vault = VaultStorage(str(resolved_project_path))
    mappings: dict = {}
    session = None

    with ExitStack() as stack:
        processing_file = original_file
        if convert_legacy and original_ext in LEGACY_OFFICE_EXTENSIONS:
            try:
                temp_dir = stack.enter_context(tempfile.TemporaryDirectory(prefix="vibemask_"))
                processing_file = convert_to_ooxml(
                    original_file,
                    output_dir=Path(temp_dir),
                    soffice_path=str(soffice) if soffice else None,
                )
                console.print(
                    f"[dim]Converted legacy file to {processing_file.name} for lossless restoration[/dim]"
                )
            except Exception as e:
                if allow_lossy:
                    console.print(
                        "[yellow]Warning:[/yellow] LibreOffice conversion failed; "
                        "falling back to lossy legacy restoration."
                    )
                    console.print(f"[dim]{e}[/dim]")
                    processing_file = original_file
                else:
                    console.print(
                        f"[red]Error:[/red] LibreOffice conversion required for {original_ext} "
                        "to preserve formatting."
                    )
                    console.print(f"[dim]{e}[/dim]")
                    console.print(
                        "[dim]Install LibreOffice or pass --soffice, or use --allow-lossy/--no-convert-legacy.[/dim]"
                    )
                    raise typer.Exit(1)

        # Initialize Processor (on the possibly converted file)
        try:
            processor = factory.get_processor(processing_file)
            processor.load()
            processor.extract_segments()
        except Exception as e:
            console.print(f"[red]Error loading file:[/red] {e}")
            raise typer.Exit(1)

        console.print(f"[dim]Project: {resolved_project_path}[/dim]")

        # 1. Resolve Session / Mappings
        if session_id:
            session = vault.get_session(session_id)
            if not session:
                console.print(f"[red]Error:[/red] Session not found: {session_id}")
                raise typer.Exit(1)
            mappings = session.mappings
            console.print(f"[cyan]📖 Using session:[/cyan] {session_id}")
        else:
            console.print("[dim]Searching for matching session...[/dim]")

            # Fast path: direct lookup by input/output file path
            direct_session = vault.find_latest_session_for_file(str(original_file.absolute()))
            if direct_session:
                session = direct_session
                mappings = direct_session.mappings
                console.print(
                    f"[cyan]📖 Auto-detected session:[/cyan] {direct_session.session_id} [dim](path match)[/dim]"
                )
            else:
                # Content-based matching against recent sessions
                text_features = ""
                try:
                    text_features = processor.get_combined_text()[:1000]
                except Exception:
                    text_features = ""

                recent_sessions = vault.list_sessions(limit=10)
                best_session = None
                max_matches = 0

                for sess in recent_sessions:
                    matches = 0
                    for masked_token in sess.mappings.keys():
                        if masked_token and masked_token in text_features:
                            matches += 1
                    if matches > 0 and matches > max_matches:
                        max_matches = matches
                        best_session = sess

                if best_session:
                    session = best_session
                    mappings = best_session.mappings
                    console.print(
                        f"[cyan]📖 Auto-detected session:[/cyan] {best_session.session_id} [dim]({max_matches} matches)[/dim]"
                    )
                else:
                    console.print(
                        "[yellow]Warning:[/yellow] No specific session matched. Using global project mappings (might be inaccurate)."
                    )
                    mappings = vault.get_all_mappings()

        if not mappings:
            console.print("[yellow]No mappings found to restore.[/yellow]")
            return
        
        # 3. Preview Changes
        count = processor.count_potential_replacements(mappings)
    
        if count == 0:
            console.print("[yellow]No restore targets found in file.[/yellow]")
            return
        
        console.print(f"[cyan]🔄 Restore Preview:[/cyan] Found {count} masked entities")
    
        if dry_run:
            return
        
        # 4. Apply Restore
        try:
            processor.replace_text(mappings)

            output_provided = output is not None
            final_output = output if output_provided else original_file.with_stem(
                original_file.stem.replace("_masked", "") + "_restored"
            )
            if (not output_provided) and final_output.name == original_file.name:
                final_output = original_file.with_stem(original_file.stem + "_restored")

            session_legacy_ext = None
            if session:
                for p in session.input_files:
                    ext = Path(p).suffix.lower()
                    if ext in LEGACY_OFFICE_EXTENSIONS:
                        session_legacy_ext = ext
                        break

            want_legacy_output = False
            final_output_ext = final_output.suffix.lower()
            if output_provided and final_output_ext in LEGACY_OFFICE_EXTENSIONS:
                want_legacy_output = True
            elif (not output_provided) and keep_legacy:
                if session_legacy_ext:
                    want_legacy_output = True
                    final_output = final_output.with_suffix(session_legacy_ext)
                elif original_ext in LEGACY_OFFICE_EXTENSIONS:
                    want_legacy_output = True

            if want_legacy_output:
                legacy_target_ext = final_output.suffix.lower()
                if legacy_target_ext not in LEGACY_OFFICE_EXTENSIONS:
                    console.print(
                        f"[red]Error:[/red] Unsupported legacy output format: {legacy_target_ext}"
                    )
                    raise typer.Exit(1)

                legacy_target = legacy_target_ext[1:]

                with tempfile.TemporaryDirectory(prefix="vibemask_out_") as out_tmp:
                    ooxml_ext = LEGACY_TO_OOXML_EXTENSION.get(
                        legacy_target_ext, processor.file_path.suffix
                    )
                    ooxml_path = Path(out_tmp) / f"{final_output.stem}{ooxml_ext}"
                    processor.save(ooxml_path)
                    saved_path = convert_back_to_legacy(
                        ooxml_path,
                        legacy_target,
                        output_path=final_output,
                        soffice_path=str(soffice) if soffice else None,
                    )
            else:
                # Default: preserve OOXML when starting from legacy.
                if original_ext in LEGACY_OFFICE_EXTENSIONS and final_output.suffix.lower() in LEGACY_OFFICE_EXTENSIONS:
                    final_output = final_output.with_suffix(LEGACY_TO_OOXML_EXTENSION[original_ext])

                saved_path = processor.save(final_output)

            console.print(f"[green]✓[/green] Restored file saved: {saved_path}")

        except Exception as e:
            console.print(f"[red]Error applying restoration:[/red] {e}")
            import traceback
            console.print(traceback.format_exc())
            raise typer.Exit(1)


@app.command()
def sessions(
    limit: int = typer.Option(10, "-n", "--limit", help="Number of sessions to show"),
    project_root: Optional[Path] = typer.Option(
        None,
        "--project-root",
        help="Vault project root. Defaults to the current folder.",
    ),
):
    """
    📜 List masking sessions.
    """
    from .vault.storage import VaultStorage

    project_path = get_project_path(project_root)
    vault = VaultStorage(str(project_path))
    session_list = vault.list_sessions(limit=limit)
    
    if not session_list:
        console.print("[yellow]No sessions found.[/yellow]")
        return
    
    table = Table(title="📜 Recent Sessions")
    table.add_column("ID", style="cyan")
    table.add_column("Created", style="dim")
    table.add_column("Status", style="green")
    table.add_column("Entities")
    
    for s in session_list:
        stats_str = ", ".join(f"{k}:{v}" for k, v in s.stats.items())
        table.add_row(
            s.session_id,
            s.created_at[:19],
            s.status,
            stats_str
        )
    
    console.print(table)


@app.command()
def status(
    project_root: Optional[Path] = typer.Option(
        None,
        "--project-root",
        help="Vault project root. Defaults to the current folder.",
    ),
):
    """
    📊 Show vault status and statistics.
    """
    from .vault.storage import VaultStorage
    
    project_path = get_project_path(project_root)
    vault = VaultStorage(str(project_path))
    stats = vault.get_stats()
    
    console.print(Panel.fit(
        f"[bold]🎭 VibeMask Status[/bold]\n\n"
        f"[cyan]Vault:[/cyan] {stats['vault_path']}\n"
        f"[cyan]Project ID:[/cyan] {stats['project_id']}\n\n"
        f"[bold]Encryption:[/bold]\n"
        f"  Algorithm: {stats['encryption']}\n"
        f"  Key store: {stats['key_provider']}\n"
        f"  Format version: {stats['encryption_version']}\n\n"
        f"[bold]Mappings:[/bold]\n" +
        "\n".join(f"  {k}: {v}" for k, v in stats['mappings_by_type'].items()) +
        f"\n  [dim]Total: {stats['total_mappings']}[/dim]\n\n"
        f"[bold]Sessions:[/bold]\n" +
        "\n".join(f"  {k}: {v}" for k, v in stats['sessions_by_status'].items()) +
        f"\n  [dim]Total: {stats['total_sessions']}[/dim]"
    ))


@app.command()
def ui(
    port: int = typer.Option(8765, "-p", "--port", help="Port to run UI on"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
):
    """
    🌐 Start the web UI.
    """
    from .web.server import start_server
    
    console.print("[bold]🎭 VibeMask Web UI[/bold]")
    console.print(f"Starting server at [cyan]http://{host}:{port}[/cyan]")
    
    start_server(host=host, port=port)


@app.command()
def run(
    cmd: List[str] = typer.Argument(..., help="Command to wrap"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without executing"),
    project_root: Optional[Path] = typer.Option(
        None,
        "--project-root",
        help="Vault project root. Defaults to the current folder.",
    ),
):
    """
    🚀 Wrap an AI tool with automatic masking/restoration.
    
    Example: vibemask run -- claude-code "fix the bug"
    """
    import subprocess
    from .vault.storage import VaultStorage
    from .restore.git import restore_changed_files, summarize_restoration
    
    if not cmd:
        console.print("[red]Error:[/red] No command specified")
        raise typer.Exit(1)
    
    console.print("[bold]🎭 VibeMask Wrapper[/bold]")
    console.print(f"[dim]Command: {' '.join(cmd)}[/dim]\n")
    
    if dry_run:
        console.print("[yellow]Dry run mode - not executing command[/yellow]")
        return
    
    project_path = get_project_path(project_root)
    vault = VaultStorage(str(project_path))
    
    # Execute command
    console.print("[cyan]▶ Running command...[/cyan]\n")
    
    try:
        result = subprocess.run(
            cmd,
            cwd=project_path,
            capture_output=False,
        )
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] Command not found: {cmd[0]}")
        raise typer.Exit(1)
    
    console.print(f"\n[cyan]▶ Command finished (exit code: {result.returncode})[/cyan]")
    
    # Restore git changes
    mappings = vault.get_all_mappings()
    
    if mappings:
        console.print("[cyan]▶ Checking for files to restore...[/cyan]")
        
        results = restore_changed_files(project_path, mappings)
        summary = summarize_restoration(results)
        
        if summary['total_changes'] > 0:
            console.print(f"[green]✓[/green] Restored {summary['total_changes']} masked tokens in {summary['successful']} files")
        else:
            console.print("[dim]No masked tokens found in changed files[/dim]")


@app.command(name="exec", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def exec_(
    ctx: typer.Context,
    tool: str = typer.Argument(..., help="AI CLI to run, such as codex, claude, or claude-yolo"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the masked command without executing"),
    project_root: Optional[Path] = typer.Option(
        None,
        "--project-root",
        help="Vault project root. Defaults to the current folder.",
    ),
    privacy_checkpoint: Optional[Path] = typer.Option(
        None,
        "--privacy-checkpoint",
        help="MLX Privacy Filter checkpoint directory",
    ),
    no_restore: bool = typer.Option(
        False,
        "--no-restore",
        help="Do not restore masked tokens in changed git files after the tool exits",
    ),
):
    """
    🚀 Run an AI CLI with automatic prompt masking and post-run restoration.

    Examples:
      vibemask -- codex "fix bug for 张三"
      vibemask -- claude -p "summarize 13812345678"
    """
    import subprocess

    from .core.merger import merge_spans
    from .core.replacer import apply_replacements, Replacement
    from .core.span import EntityType
    from .masker.placeholder import PlaceholderGenerator
    from .restore.git import get_changed_files, restore_file, summarize_restoration
    from .vault.storage import VaultStorage

    raw_args = list(ctx.args)
    project_path = get_project_path(project_root)
    vault = VaultStorage(str(project_path))

    detector = None
    generator = PlaceholderGenerator()

    masked_args: list[str] = []
    replacements: dict[str, str] = {}
    stats: dict[str, int] = {}

    for arg in raw_args:
        if not _should_mask_cli_arg(arg):
            masked_args.append(arg)
            continue

        if detector is None:
            from .detector.hybrid import HybridDetector

            detector = HybridDetector(
                privacy_backend="mlx",
                privacy_checkpoint=privacy_checkpoint,
                chinese_names_enabled=True,
            )
        spans = merge_spans(detector.detect(arg), arg)
        if not spans:
            masked_args.append(arg)
            continue

        arg_replacements = []
        for span in spans:
            try:
                entity_type = span.type
            except ValueError:
                entity_type = EntityType.UNKNOWN

            proposed = generator.generate(span.text, entity_type)
            masked = vault.get_or_create_mapping(
                original=span.text,
                entity_type=span.type.value,
                masked=proposed,
                source=span.source.value,
                confidence=span.confidence,
            )
            replacements[span.text] = masked
            stats[span.type.value] = stats.get(span.type.value, 0) + 1
            arg_replacements.append(
                Replacement(
                    start=span.start,
                    end=span.end,
                    original=span.text,
                    masked=masked,
                )
            )

        masked_args.append(apply_replacements(arg, arg_replacements))

    command = [tool, *masked_args]
    console.print("[bold]🎭 VibeMask Exec[/bold]")
    console.print(f"[dim]Project: {project_path}[/dim]")
    console.print(f"[dim]Command: {' '.join(command)}[/dim]")
    if replacements:
        console.print(f"[green]Masked {len(replacements)} unique prompt value(s).[/green]")
    else:
        console.print("[dim]No prompt values needed masking.[/dim]")

    if dry_run:
        return

    changed_before = {path.resolve() for path in get_changed_files(project_path)}
    session_id = None
    if replacements:
        session_id = vault.create_session(
            input_files=[],
            output_files=[],
            mappings={masked: original for original, masked in replacements.items()},
            stats=stats,
            status="pending",
        )

    try:
        result = subprocess.run(command, cwd=project_path, capture_output=False)
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] Command not found: {tool}")
        raise typer.Exit(1)

    if not no_restore:
        mappings = vault.get_all_mappings()
        if mappings:
            changed_after = {path.resolve() for path in get_changed_files(project_path)}
            restore_targets = sorted(changed_after - changed_before)
            results = [restore_file(path, mappings) for path in restore_targets]
            summary = summarize_restoration(results)
            if summary["total_changes"] > 0:
                console.print(
                    f"[green]✓[/green] Restored {summary['total_changes']} masked token(s) "
                    f"in {summary['successful']} file(s)"
                )

    if session_id:
        console.print(f"[dim]Session ID: {session_id}[/dim]")

    if result.returncode:
        raise typer.Exit(result.returncode)


def _should_mask_cli_arg(arg: str) -> bool:
    if not arg or arg.startswith("-"):
        return False
    if len(arg) < 2:
        return False
    if any("\u4e00" <= ch <= "\u9fff" for ch in arg):
        return True
    if any(ch.isdigit() for ch in arg):
        return True
    if "@" in arg or "://" in arg:
        return True
    return any(ch.isspace() for ch in arg) and any(ch.isalpha() for ch in arg)


@app.command("shell-init")
def shell_init(
    shell: str = typer.Option("zsh", "--shell", help="Shell type: zsh | bash"),
):
    """
    Print shell functions that wrap codex/claude with VibeMask.

    Usage:
      eval "$(vibemask shell-init)"
    """
    if shell not in {"zsh", "bash"}:
        console.print(f"[red]Error:[/red] Unsupported shell: {shell}. Use zsh | bash.")
        raise typer.Exit(1)

    script = r'''
# VibeMask AI CLI wrappers. Add this with:
#   eval "$(vibemask shell-init)"
if command -v codex >/dev/null 2>&1; then
  _vibemask_codex_bin="$(command -v codex)"
  codex() {
    vibemask -- "$_vibemask_codex_bin" "$@"
  }
fi

if command -v claude >/dev/null 2>&1; then
  _vibemask_claude_bin="$(command -v claude)"
  claude() {
    vibemask -- "$_vibemask_claude_bin" "$@"
  }
  claude-yolo() {
    vibemask -- "$_vibemask_claude_bin" --dangerously-skip-permissions "$@"
  }
fi
'''
    console.print(script.strip())


def main():
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
