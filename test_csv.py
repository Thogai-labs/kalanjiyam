from kalanjiyam.app import create_app
from kalanjiyam import db
from kalanjiyam.models.batch import BatchJob, BatchItem, BatchOcrPage
app = create_app('development')
with app.app_context():
    job = db.session.query(BatchJob).filter_by(job_type='UI_BATCH_TRANSLATION').order_by(BatchJob.id.desc()).first()
    if job:
        for item in job.items:
            print(f"Item translation data size: {item.translation_data_size_bytes}")
            for p in item.pages[:3]:
                print(f"  Page {p.page_number} trans data: {p.translation_data_size_bytes}")
