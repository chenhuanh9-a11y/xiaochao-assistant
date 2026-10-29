"""Display-only integration. Cold preparation belongs to the existing pose worker."""
from base_map_display import BaseMapDisplay
from custom_maps import CustomMapLibrary
from functools import wraps
import numpy as np
from route_display_notice import DisplayNoticeContext, FallbackNoticeTracker, display_feedback


class DisplayPixels(np.ndarray):
    """A display canvas retaining its small provenance record through copies.

    No extra image allocation or global image registry; queued copies cannot
    lose the captured generation merely because a newer frame was rendered.
    """
    def __array_finalize__(self, source):
        self.display_descriptor = getattr(source, 'display_descriptor', None)


def display_source_guard(method):
    """Keep captured provenance with real pixels through queued presentation."""
    @wraps(method)
    def guarded(self, image, *args, **kwargs):
        descriptor = self._display_output_metadata(image)
        if descriptor and not self._display_source_is_current(descriptor):
            return False
        shown = method(self, image, *args, **kwargs)
        if shown and descriptor and self._display_source_is_current(descriptor):
            # Presentation is not final transaction acceptance. The final pose
            # consumer alone commits this record and can consume a notice.
            # Frame/pose reuse is adjudicated by the existing compositor. Only
            # its successful presentation may renew the display-only context.
            context = descriptor['displayContext']
            self._presented_display = dict(descriptor,
                displayContext=self._display_notice_context(context.map_id, context.floor))
        return shown
    return guarded


class BaseDisplayIntegrationMixin:
    def _invalidate_base_display(self):
        # Replace the provider so a worker that starts late cannot publish into
        # the newly installed resource context, even after a clear/start race.
        self._base_display_epoch = getattr(self, '_base_display_epoch', 0) + 1
        self._base_map_display = BaseMapDisplay()
        self._committed_display = None
        self._presented_display = None
        self._committed_reference_display = None
        self._prepared_display_pixels = None
        cache = getattr(self, 'sheet_photo_cache', None)
        if cache is not None:
            cache.clear()

    def _base_author_status(self, map_id, floor):
        preferred = getattr(self, 'preferred_route_profile_id', None)
        catalog = getattr(self, 'author_route_catalog', None)
        variants = catalog.variants(preferred, str(map_id), include_inactive=True) if catalog else ()
        declared = any(str(floor) in row.layers for row in variants)
        optional = getattr(self, 'optional_author_displays', None)
        binding = optional.binding(preferred, map_id, floor) if optional else None
        if not declared and binding is None:
            return 'missing'
        if binding is not None and not binding.get('usable'):
            return 'registration-failed'
        return 'available'

    def _prepare_base_map_display(self, map_id, floor, reference):
        """Called only inside the existing bounded first-pose worker."""
        provider = getattr(self, '_base_map_display', None)
        if (provider is None or reference is None
                or getattr(self, 'game_mode', None) != 'nightmare'
                or getattr(self, 'difficult_native_context', None) is not None):
            return
        # Prepare the current floor even when the selected author exists. A
        # later author switch can then repaint from memory without another fit.
        library = getattr(provider, '_consumer_library', None)
        if library is None:
            library = CustomMapLibrary(self.custom_map_library().root)
            provider._consumer_library = library
        provider.resolve(library, map_id, floor, reference)

    def _peek_base_map_display(self, map_id, floor, reference):
        provider = getattr(self, '_base_map_display', None)
        library = getattr(provider, '_consumer_library', None)
        if (provider is None or library is None
                or getattr(self, 'game_mode', None) != 'nightmare'
                or getattr(self, 'difficult_native_context', None) is not None):
            return None
        return provider.peek(library, map_id, floor, reference).layer

    def _display_notice_context(self, map_id, floor):
        return DisplayNoticeContext(
            getattr(self, '_display_round_epoch', None),
            str(getattr(self, 'game_mode', '')), str(map_id), str(floor),
            (getattr(self, '_base_display_epoch', 0),
             getattr(self, 'preferred_route_profile_id', None)),
            (getattr(self, 'active_map_cycle_id', None),
             getattr(self, 'current_frame_generation', None),
             getattr(self, 'surface_visibility_generation', None)))

    def _display_descriptor(self, layer, map_id, floor):
        return {**{key: layer.get(key) for key in (
            'displayKind', 'displayLabel', 'displayResourceKey', 'authorStatus')},
            'displayContext': layer.get('displayContext', self._display_notice_context(map_id, floor))}

    def _display_descriptor_is_current(self, descriptor):
        context = descriptor.get('displayContext')
        return bool(context and context == self._display_notice_context(context.map_id, context.floor))

    def _display_source_is_current(self, descriptor):
        context = descriptor.get('displayContext')
        if context is None:
            return False
        current = self._display_notice_context(context.map_id, context.floor)
        return (context.mode == current.mode and context.round_id == current.round_id
                and context.resource_generation == current.resource_generation)

    def _remember_display_output(self, image, descriptor):
        if image is None or not descriptor.get('displayContext'):
            return image
        output = image.view(DisplayPixels)
        output.display_descriptor = {key: descriptor.get(key) for key in (
            'displayKind', 'displayLabel', 'displayResourceKey', 'authorStatus', 'displayContext')}
        return output

    def _display_output_metadata(self, image):
        value = getattr(image, 'display_descriptor', None)
        return dict(value) if value else None

    def _record_display_commit(self, metadata, map_id, floor):
        if metadata.get('displayContext') != self._display_notice_context(map_id, floor):
            return False
        self._committed_display = dict(metadata)
        return True

    def _current_display_descriptor(self, map_id, floor):
        value = getattr(self, '_committed_display', None) or {}
        return value if value.get('displayContext') == self._display_notice_context(map_id, floor) else {}

    def _begin_display_round(self):
        self._display_round_epoch = getattr(self, '_display_round_epoch', 0) + 1
        tracker = getattr(self, '_fallback_notice_tracker', None)
        if tracker is None:
            tracker = self._fallback_notice_tracker = FallbackNoticeTracker()
        tracker.begin_round(self._display_round_epoch)

    def _committed_display_notice(self, map_id, floor):
        value = self._current_display_descriptor(map_id, floor)
        tracker = getattr(self, '_fallback_notice_tracker', None)
        if not value or tracker is None:
            return ''
        return tracker.consume(value['displayContext'], self._display_notice_context(map_id, floor),
            display_kind=value.get('displayKind'), author_status=value.get('authorStatus'),
            current_commit=True)

    def _committed_display_feedback(self, map_id, floor):
        value = self._current_display_descriptor(map_id, floor)
        return display_feedback(value.get('displayKind'), value.get('authorStatus'))

    def _note_reference_fallback_committed(self, descriptor, *, provisional=False):
        """Actual fixed-map pixels may supply the one notice after pose acceptance."""
        context = descriptor.get('displayContext')
        tracker = getattr(self, '_fallback_notice_tracker', None)
        if (context is None or tracker is None or provisional
                or not self._display_descriptor_is_current(descriptor)
                or not getattr(self, 'round_work_active', False)
                or not (getattr(self, 'current_floor_authorization', None) or {}).get('poseVerified')
                or not self._current_floor_authorization_is_current(context.map_id, context.floor)):
            return
        notice = tracker.consume(context, self._display_notice_context(context.map_id, context.floor),
            display_kind=descriptor.get('displayKind'), author_status=descriptor.get('authorStatus'),
            current_commit=True)
        if notice:
            self._ensure_route_notification_context()
            self._route_notified_floors.add((context.map_id, context.floor))
            self._route_notified_identities.add(context.map_id)
            self._publish_route_notification(notice)
