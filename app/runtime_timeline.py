"""Observational context and intervals. No Tk, geometry or authorization imports."""
from contextvars import ContextVar
from functools import wraps
from itertools import count
import inspect
import time

_scope = ContextVar('runtime_timeline_scope', default=None)
_sink = ContextVar('runtime_timeline_sink', default=None)
_frame = ContextVar('runtime_timeline_input_frame', default=None)
_attempts = count(1)


def context():
    return dict(_scope.get() or {})


def bind_input(identity, frame, binding):
    """Keep source identity separate from the actual object being measured."""
    known = frame is not None and binding is not None and frame is binding[0]
    identity.update(inputFrameGeneration=binding[1] if known else None,
                    inputFrameCycleId=binding[2] if known else None,
                    inputFrameBinding=binding[3] if known else 'unknown')


def floor_vote_evidence(window, observed, strong, explicit):
    window = list(window[-5:])
    return dict(observationWindow=window,
                matchingVoteCount=sum(v == observed for v in window) if observed in (-1, 1, 2) else 0,
                tailTwoMatch=bool(len(window)>=2 and observed in (-1,1,2) and window[-2:]==[observed,observed]),
                requiredWindowMatches=None if explicit else 1,
                requiredTailMatches=None if explicit else 1,
                explicitIntentBypass=explicit)


class RuntimeTimeline:
    """Owned by the existing log lock; never reads mutable application state."""
    def __init__(self):
        self.sequence = 0

    def enrich(self, event, values):
        self.sequence += 1
        # Explicit legacy event fields retain their old meaning. traceContext
        # independently preserves the immutable initiating identity.
        row = dict(values)
        scope = context()
        if scope:
            row.setdefault('traceContext', scope)
        row['timelineSequence'] = self.sequence
        row['loggedMonotonicAt'] = time.perf_counter()
        return row


def observe(stage, *, owner='current'):
    """One small end record per real call; nested calls inherit origin identity."""
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def measured(self, *args, **kwargs):
            if getattr(self, 'runtime_timeline_enabled', True) is False:
                return function(self, *args, **kwargs)
            if not hasattr(self, 'log_lock') and '_log' not in getattr(self, '__dict__', {}):
                return function(self, *args, **kwargs)
            bound = signature.bind(self, *args, **kwargs).arguments
            inherited = context()
            binding = _frame.get()
            if owner == 'worker':
                worker_args = bound['args']
                token = worker_args[0]
                base = worker_args[-1] if len(worker_args) >= 10 else {}
                base = base if isinstance(base, dict) else {}
                identity = dict(base.get('_timeline') or {})
                identity.update(cycleId=token[0], mapId=token[1], floor=token[2], sourceFrameGeneration=token[3], resetGeneration=token[5])
                binding = (worker_args[4], token[3], token[0], 'worker-owned-frame')
                bind_input(identity, worker_args[4], binding)
            elif owner == 'capture':
                identity = dict(bound.get('_timeline') or {})
                identity.setdefault('cycleId', bound.get('token'))
            else:
                event = bound.get('event')
                identity = dict((event or {}).get('_timeline') or inherited or self._runtime_trace_context())
                actual_frame = bound.get('frame', getattr(self, 'current_frame', None))
                if actual_frame is not None and actual_frame is getattr(self, 'current_frame', None):
                    binding = (actual_frame, getattr(self, 'current_frame_generation', None),
                               getattr(self, 'active_map_cycle_id', None), 'current-frame-object')
                bind_input(identity, actual_frame, binding)
                if identity['inputFrameBinding'] == 'unknown':
                    binding = None
            if stage == 'validation':
                identity['routeProfileId'] = bound.get('profile_id')
                matrix = bound.get('render_matrix_source_to_client')
                identity['validationMatrix'] = matrix.tolist() if getattr(matrix, 'shape', None) == (3,3) else None
            identity['parentAttempt'] = identity.get('traceAttempt')
            identity['traceAttempt'] = next(_attempts)
            identity['callSite'] = function.__name__
            started = time.perf_counter()
            marker = _scope.set(identity)
            sink_marker = _sink.set(self._log)
            frame_marker = _frame.set(binding)
            result = None
            error = None
            try:
                result = function(self, *args, **kwargs)
                return result
            except Exception as exc:
                error = type(exc).__name__
                raise
            finally:
                ended = time.perf_counter()
                detail = result[0] if isinstance(result, tuple) and result and isinstance(result[0], dict) else result
                outcome = {k: detail[k] for k in ('success', 'verified', 'reason', 'handled') if k in detail} if isinstance(detail, dict) else {}
                if isinstance(result, dict) and owner == 'worker':
                    result['_timeline'] = dict(identity)
                try:
                    self._log('runtime-stage-completed', cycleId=identity.get('cycleId'),
                              stage=stage, startedMonotonicAt=started, endedMonotonicAt=ended,
                              occurredMonotonicAt=ended, elapsedMs=(ended-started)*1000,
                              outcome=outcome, error=error, traceContext=dict(identity))
                finally:
                    _scope.reset(marker)
                    _sink.reset(sink_marker)
                    _frame.reset(frame_marker)
        return measured
    return decorate


def observe_function(stage):
    """Pure validators emit only inside an observed production operation."""
    def decorate(function):
        @wraps(function)
        def measured(*args, **kwargs):
            logger = _sink.get()
            if logger is None:
                return function(*args, **kwargs)
            identity = context()
            actual_frame = kwargs.get('frame', args[0] if args else None)
            bind_input(identity, actual_frame, _frame.get())
            if stage == 'validation':
                identity['routeProfileId'] = kwargs.get('route_profile_id')
            identity.update(parentAttempt=identity.get('traceAttempt'), traceAttempt=next(_attempts), callSite=function.__name__)
            matrix = kwargs.get('matrix_source_to_client', args[2] if len(args)>2 else None)
            if stage == 'validation':
                identity['validationMatrix'] = matrix.tolist() if getattr(matrix, 'shape', None) == (3,3) else None
            marker = _scope.set(identity)
            started = time.perf_counter()
            result = None
            try:
                result = function(*args, **kwargs)
                return result
            finally:
                ended = time.perf_counter()
                try:
                    logger('runtime-stage-completed', cycleId=identity.get('cycleId'), stage=stage,
                           startedMonotonicAt=started, endedMonotonicAt=ended, occurredMonotonicAt=ended,
                           elapsedMs=(ended-started)*1000, traceContext=identity,
                           outcome={k:result[k] for k in ('verified','reason','selectedFloor','margin','layout') if k in result} if isinstance(result,dict) else {})
                finally:
                    _scope.reset(marker)
        return measured
    return decorate
