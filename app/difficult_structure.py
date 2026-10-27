"""Static difficult author pixels: architecture and obscured ink stay separate.

The completed occupancy is only a proposal canvas. No completed pixel is a
measured wall: known_boundary explicitly excludes the ink and its edge guard.
"""
import cv2
import numpy as np
from nightmare_candidate import author_route_structure_mask, map_color_mask


def bracketed_room_preview(labels, proposal_mask):
    """Display-only fill between matching measured colours in one alpha run.

    No source wall, room measurement or unknown flag is changed. The bracket
    terminates at an original empty pixel, so it cannot bridge a real gap.
    """
    neighbours=[]
    for axis in (0,1):
        values=np.moveaxis(labels,axis,0)
        occupied=np.moveaxis(proposal_mask>0,axis,0)
        pair=[]
        for reverse in (False,True):
            result=np.zeros_like(values);last=np.zeros(values.shape[1],np.uint8)
            indexes=range(len(values)-1,-1,-1) if reverse else range(len(values))
            for i in indexes:
                last[~occupied[i]]=0
                measured=values[i]>0;last[measured]=values[i][measured]
                result[i]=last
            pair.append(np.moveaxis(result,0,axis))
        neighbours.append(np.where((pair[0]>0)&(pair[0]==pair[1]),pair[0],0))
    horizontal,vertical=neighbours
    agreed=np.where(horizontal>0,horizontal,vertical)
    agreed[(horizontal>0)&(vertical>0)&(horizontal!=vertical)]=0
    recovered=(labels==0)&(agreed>0)&(proposal_mask>0)
    preview=labels.copy();preview[recovered]=agreed[recovered]
    return preview,recovered


def structure_channels(route, *, native_reference=False):
    if route.ndim != 3 or (route.shape[2] != 4 and not native_reference):
        raise ValueError('Difficult structure requires the original RGBA canvas')
    hsv = cv2.cvtColor(route[:,:,:3], cv2.COLOR_BGR2HSV)
    architecture = (map_color_mask(route[:,:,:3],live=False) if native_reference else
                    author_route_structure_mask(route, profile_id='zhan10-speed'))
    opaque = route[:,:,3] >= 128 if route.shape[2]==4 else np.ones(route.shape[:2],dtype=bool)
    # Reuse the author's existing chromatic-ink boundary. White symbols and
    # text are also unknown, never interior holes or architectural edges.
    white = opaque & (hsv[:,:,1] < 40) & (hsv[:,:,2] > 205)
    # White architectural outlines border both painted floor and empty canvas.
    # Interior white legends do not. Keep this distinction independent of any
    # game frame or estimated pose instead of deleting every bright wall.
    colored_floor=architecture & (hsv[:,:,1]>=40)
    empty=(~opaque | np.all(route[:,:,:3]==0,axis=2)).astype(np.uint8)
    outline=(cv2.dilate(colored_floor,np.ones((5,5),np.uint8))>0) & (cv2.dilate(empty,np.ones((5,5),np.uint8))>0)
    ink = opaque & ((hsv[:,:,1] >= 140) | (white & ~outline))
    if native_reference:
        ink |= (hsv[:,:,1]>=82) & (hsv[:,:,2]>=105)
    unknown = cv2.dilate(ink.astype(np.uint8), np.ones((5,5),np.uint8))
    # Dark watermark strokes and detached neutral labels are not rooms.
    # Their original pixels remain inspectable, but never become clean walls.
    dark = opaque & (hsv[:,:,2] < 68) & np.any(route[:,:,:3]!=0,axis=2)
    unknown |= dark.astype(np.uint8)
    if native_reference:
        completed = cv2.inpaint(architecture*255, unknown*255, 3, cv2.INPAINT_NS)
        mask = (completed >= 128).astype(np.uint8)
    else:
        # The exported alpha already contains the continuous floor silhouette.
        # Keep it for proposals without turning internal route strokes into
        # holes. Ink that protrudes beyond walls remains explicitly unknown.
        mask = (opaque & np.any(route[:,:,:3]!=0,axis=2)).astype(np.uint8)
    mask = cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
    edge = cv2.morphologyEx(mask, cv2.MORPH_GRADIENT, np.ones((3,3),np.uint8))
    known = (edge>0) & (cv2.dilate(unknown,np.ones((3,3),np.uint8))==0)
    # Room fill is a measured categorical layer, not an inpainted picture.
    # Vivid route/legend pixels remain unknown instead of borrowing a nearby
    # room colour. Quantization removes watermark texture without inventing
    # a hidden internal boundary.
    room_labels=np.zeros(mask.shape,np.uint8)
    measured=(architecture>0)&(unknown==0)&(mask>0)
    room_labels[measured]=1
    room_labels[measured&(hsv[:,:,0]>=80)&(hsv[:,:,0]<=135)&(hsv[:,:,1]>=25)]=2
    room_labels[measured&((hsv[:,:,0]<=32)|(hsv[:,:,0]>=160))&(hsv[:,:,1]>=25)]=3
    # Interior bright geometry includes stairs, doors and symbol fragments.
    # Keep it separately for inspection; do not promote it to wall evidence.
    interior_details=(white & ~outline).astype(np.uint8)
    return dict(mask=mask, unknown=unknown, known_boundary=known,
                room_labels=room_labels,interior_details=interior_details,
                matrixSourceToAuthor=np.eye(3))
