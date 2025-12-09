from kalanjiyam.tasks import app
from kalanjiyam import queries as q
from kalanjiyam import database as db
from kalanjiyam.models.proofing import OCRComparison
from kalanjiyam.utils.ocr_engine import run_ocr
from kalanjiyam.utils.assets import get_page_image_filepath
from kalanjiyam.enums import SitePageStatus
from config import create_config_only_app
import jiwer
import Levenshtein
import json
from datetime import datetime

@app.task(bind=True)
def run_ocr_comparison_task(self, comparison_id, app_env):
    """Run OCR comparison for a project."""
    
    flask_app = create_config_only_app(app_env)
    with flask_app.app_context():
        session = q.get_session()
        comparison = session.query(OCRComparison).get(comparison_id)
        if not comparison:
            return

        comparison.status = "in_progress"
        session.commit()

        try:
            project = comparison.project
            
            # Fetch statuses IDs
            r1_status = session.query(db.PageStatus).filter_by(name=SitePageStatus.R1).first()
            r2_status = session.query(db.PageStatus).filter_by(name=SitePageStatus.R2).first()
            
            target_status_ids = []
            if r1_status: target_status_ids.append(r1_status.id)
            if r2_status: target_status_ids.append(r2_status.id)
            
            if not target_status_ids:
                comparison.status = "failed"
                comparison.error_message = "Could not find R1/R2 statuses."
                session.commit()
                return

            # Fetch pages
            pages = [p for p in project.pages if p.status_id in target_status_ids]
            
            results = []
            total_pages = len(pages)
            processed_count = 0
            
            total_wer = 0.0
            total_cer = 0.0
            total_lev = 0
            
            if total_pages == 0:
                comparison.status = "completed"
                comparison.summary_metrics = {
                    'avg_wer': 0,
                    'avg_cer': 0,
                    'total_pages': 0,
                    'processed_pages': 0,
                    'note': 'No proofed pages found to compare.'
                }
                session.commit()
                return

            for page in pages:
                # Get ground truth from latest revision
                if not page.revisions:
                    continue
                
                # Sort revisions just in case (though relationship is ordered)
                # But Page.revisions is ordered by created (oldest first)
                ground_truth = page.revisions[-1].content
                
                if not ground_truth.strip():
                    continue

                # Prepare OCR run
                image_path = get_page_image_filepath(project.slug, page.slug)
                engine = comparison.engine
                
                gpu_config = None
                if engine in ['surya', 'nanonets', 'deepseek', 'chandra', 'qwen3']:
                     gpu_config = {'device': 'auto'} 
                     if engine == 'surya':
                         from kalanjiyam.utils.surya_gpu_config import get_gpu_config_from_env
                         gpu_config = get_gpu_config_from_env()

                # Run OCR
                # TODO: make language configurable? defaulting to 'sa'
                ocr_response = run_ocr(image_path, engine_name=engine, language='sa', gpu_config=gpu_config)
                ocr_text = ocr_response.text_content
                
                # Metrics
                wer = jiwer.wer(ground_truth, ocr_text)
                cer = jiwer.cer(ground_truth, ocr_text)
                lev_dist = Levenshtein.distance(ground_truth, ocr_text)
                
                page_result = {
                    'page_slug': page.slug,
                    'wer': wer,
                    'cer': cer,
                    'lev_dist': lev_dist,
                    'ocr_text': ocr_text, 
                    'ground_truth': ground_truth
                }
                results.append(page_result)
                
                total_wer += wer
                total_cer += cer
                total_lev += lev_dist
                
                processed_count += 1
                
                # Update progress
                comparison.status = f"in_progress ({processed_count}/{total_pages})"
                # We update page_results periodically
                comparison.page_results = results
                session.commit()
            
            # Finalize
            avg_wer = total_wer / processed_count if processed_count > 0 else 0
            avg_cer = total_cer / processed_count if processed_count > 0 else 0
            avg_lev = total_lev / processed_count if processed_count > 0 else 0
            
            comparison.summary_metrics = {
                'avg_wer': avg_wer,
                'avg_cer': avg_cer,
                'avg_lev': avg_lev,
                'total_pages': total_pages,
                'processed_pages': processed_count
            }
            comparison.status = "completed"
            session.commit()

        except Exception as e:
            session.rollback()
            # Need to get a new session or merge comparison if rollback detached it?
            # Rollback rolls back the transaction. The comparison object is still attached but attributes reverted.
            # We want to save the error.
            
            comparison = session.query(OCRComparison).get(comparison_id)
            comparison.status = "failed"
            comparison.error_message = str(e)
            session.commit()
            # Re-raise to show in Celery logs
            raise e

