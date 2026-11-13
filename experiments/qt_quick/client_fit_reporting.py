"""Reporting only: a failed structure observation is not a failed map opening.

Owner holds LiveRuntime's lifetime lock. No recognition/display authority lives
here. The owner supplies terminal boundaries and may report unfinished work
without inventing a solver verdict; this module only explains that evidence.
"""
import copy

RETRY_REASONS = {
    'initial-structure-supported-chains-insufficient': '可用结构线段不足',
    'initial-structure-descriptor-budget-unresolved': '结构候选尚未核对完成',
    'initial-structure-no-chain-ratios': '尚未找到可核对的结构比例',
    'initial-structure-validation-budget-unresolved': '结构验证尚未完成',
    'initial-structure-ambiguous': '存在多个结构候选',
    'initial-structure-current-geometry-failed': '当前画面结构未核对通过',
    'structure-current-geometry-failed': '当前画面结构未核对通过',
}


class FitReporting:
    def __init__(self):
        self.discard()

    def discard(self):
        self.scope = None
        self.report = None
        self.succeeded = False
        self.waiting = False

    def select(self, scope):
        if scope != self.scope:
            self.discard()
            self.scope = scope

    def success(self, scope):
        self.select(scope)
        self.succeeded = True
        self.report = None

    def failure(self, scope, reason, now, diagnostics):
        self.select(scope)
        if self.report is None:
            self.report = dict(attemptCount=0, firstReason=reason, lastReason=reason,
                               firstAt=now, lastAt=now, firstDiagnostics=copy.deepcopy(diagnostics))
        self.report.update(retryCount=self.report['attemptCount'],
                           attemptCount=self.report['attemptCount'] + 1,
                           lastReason=reason, lastAt=now, lastDiagnostics=copy.deepcopy(diagnostics))
        waiting = not self.succeeded and not self.waiting and now - self.report['firstAt'] >= 3.0
        self.waiting |= waiting
        return copy.deepcopy(self.report), waiting

    def close(self):
        report = copy.deepcopy(self.report) if not self.succeeded else None
        self.discard()
        return report


def incomplete_problem(event):
    report = event.get('fitReport') or {}
    first, last = report.get('firstReason'), report.get('lastReason', event.get('lastReason'))
    import json
    raw = json.dumps({'roundId': event.get('roundId'), 'cycleId': event.get('cycleId'),
                     'openingId': event.get('openingId'), 'lastReason': last,
                     'terminationReason': event.get('terminationReason'), **report}, ensure_ascii=False, default=str)
    if report.get('attemptCount'):
        first_text = RETRY_REASONS.get(first, '首次原因尚未确认')
        last_text = RETRY_REASONS.get(last, '末次原因尚未确认')
        summary = (f"本次开图尝试贴合 {report['attemptCount']} 次均未成功（重试 {report.get('retryCount', max(0, report['attemptCount'] - 1))} 次）。"
                   f"首次：{first_text}；最后：{last_text}。")
        title = '本次开图未能贴合'
    else:
        title, summary = '本次开图未完成', '开图结束前未完成识别或贴合。'
        if last:
            from client_problem_messages import operation_problem
            problem = operation_problem(dict(action='close_map', reason=last), event)
            summary += problem['summary']
    return dict(title=title, kind='warning', phase='recognition-alignment',
        action='close_map', mode=event.get('mode', ''), mapId=event.get('mapId', ''), floor=event.get('floor', ''),
        summary=summary,
        reason='map-fit-incomplete', fitReport=copy.deepcopy(report), raw=raw)
