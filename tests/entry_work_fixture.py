"""Controlled source evidence for orchestration tests; not a solver replacement."""
from entry_evidence_work import EntryEvidenceWork


class FixtureEntryWork(EntryEvidenceWork):
    def __init__(self, frame, producer):
        self.producer, self.value = producer, None
        super().__init__(frame)

    def _produce(self):
        self.value = self.producer()

    def _jobs(self):
        yield self._job(None, 'fixture-source', self._produce)

    def finish(self, *, elapsed_ms=0.):
        self.close()
        rows, diagnostics = self.value or ([], dict(reason='fixture-source-incomplete'))
        diagnostics = dict(diagnostics)
        diagnostics.setdefault('elapsedMs', elapsed_ms)
        if not self._complete:
            diagnostics.update(complete=False, verificationComplete=False)
            for row in rows:
                row['identityVerified'] = row['poseVerified'] = False
        return rows, diagnostics
