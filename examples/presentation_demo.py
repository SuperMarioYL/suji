"""Capture a local text file and invalidate its facts after a source edit."""
import json,tempfile
from pathlib import Path
from suji.capture import CapturedText,ingest_capture
from suji.llm import RuleBasedExtractor
from suji.store import SuJiStore
from suji.cascade import Cascade
with tempfile.TemporaryDirectory(prefix='suji-demo-') as directory:
    source=Path(directory)/'article.txt';source.write_text('示例项目有 12 个任务。\n版本号是 1.0。\n')
    class FileCapture:
        def capture(self):return CapturedText(source.read_text(),'local.example',str(source),'Constructed local article')
    with SuJiStore(str(Path(directory)/'facts.db')) as store:
        captured=ingest_capture(FileCapture(),RuleBasedExtractor(),store)
        before=[f.status for f in store.list_facts()]
        source.write_text('示例项目有 15 个任务。\n版本号是 1.0。\n')
        changed=Cascade(store).recheck_all()[0]
        print(json.dumps({'captured_facts':captured,'before':before,'source_mutated':changed.mutated,
            'stale_count':changed.stale_count,'after':[f.status for f in store.list_facts()],
            'diff_has_old_and_new':('12' in changed.diff and '15' in changed.diff)},ensure_ascii=False,indent=2))
        assert changed.mutated and changed.stale_count==captured
