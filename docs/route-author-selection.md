# Selecting the route actually displayed

The map resources page now separates **实际使用路线** (runtime route selection)
from **浏览来源** (preview only). The settings page's author selector uses the
same controller. Installed authors and custom galleries are scoped to the loaded
mode. The preference is saved, including its mode, and stays visible if a layer
needs a fallback.

For shared-structure modes, selection repaints through the author's existing
static coordinate bridge. It does not rank identities, run alignment, reset the
round, change canonical roots/floor relations or create a display authorization.
An existing current authorization is required to repaint an open overlay; a
closed or unverified overlay waits for normal verified presentation. The static
reference sheet follows its existing show/hide policy.

Render-basis caches are transported per floor, including shared-structure
fallbacks, while malformed optional render caches are dropped locally. Shared
recovery consumes the native-reference matrix and returns a shared-coordinate
result, which the existing owner renders with the current preference. Old
author-bound worker results are ignored before observation/failure processing;
neutral structure results retain normal current-frame verification.

If the selected author lacks the current map/floor or the layer is blocked by
its existing registration status, the page explicitly reports a structure
fallback. It does not silently select another author. Custom-gallery selection
continues to use its existing library and route semantics; selecting an author
is an explicit change of preference, not an edit to custom route data.

## Validation boundaries

Targeted controller and resource-page tests use Tk/widget IO replacements.
Independent scratch validation installed all four Q90 import packages through
the actual importer and loaded real images through the normal provider/PIL and
compositor path. Three nightmare authors produced distinct pixels with the same
native pose and core authorization, and no rank/solver calls. The known blocked
optional F2 layer stayed blocked and fell back to structure. Settings reload,
difficult-mode author availability, and base/precomputed-resource hashes were
checked without touching an installed user directory.

No live GUI clicking, game input, installation or new Setup validation is claimed.
This source change requires a future build; existing delivered installers are
unchanged. Private game captures, imported images and diagnostic logs are not
part of the source PR.
