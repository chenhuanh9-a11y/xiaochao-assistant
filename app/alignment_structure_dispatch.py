"""Original bounded independent-member solver caller shared by Tk and Qt."""
import numpy as np
import cv2
from alignment_observation import rendered_profile_residual_is_verified

def independent_member_fit(self, frame, route, map_id, floor, profile, *, cancelled=None, context=None, pose_index_only=False):
        """Read one member's own resources; never change identity or root."""
        from single_map_structure import solve_initial_structure
        product = self._author_route_clean_product_for_route(route, profile, map_id, floor)
        if (product is None or not isinstance(route, np.ndarray)
            or not isinstance(product.clean_mask, np.ndarray)
            or not isinstance(product.validation_mask, np.ndarray)
            or product.clean_mask.shape != route.shape[:2]
            or product.validation_mask.shape != route.shape[:2]):
            return {'success': False, 'reason': 'initial-structure-resource-unavailable'}
        static_channels={}
        if getattr(product,'known_boundary',None) is not None:
            static_channels=dict(source_known_boundary=product.known_boundary,source_unknown=product.unknown_mask)
        indexed_candidates=[]
        index_diagnostic={'attempted':False,'reason':'pose-index-disabled','candidateCount':0}
        if bool(getattr(self,'settings',{}).get('poseIndexCandidatesEnabled',
                getattr(self,'settings',{}).get('difficultGridEnabled',False))):
            provider=getattr(getattr(self,'engine',None),'pose_index_candidates',None)
            if callable(provider):
                try:
                    indexed_candidates,index_diagnostic=provider(frame,map_id,floor,cancelled=cancelled,context=context)
                except (ValueError,TypeError,KeyError,OSError,cv2.error) as error:
                    indexed_candidates=[];index_diagnostic=dict(attempted=True,reason='pose-index-error',error=str(error),candidateCount=0)
                if cancelled and cancelled():return {'success':False,'reason':'structure-work-obsolete'}
        if pose_index_only and not indexed_candidates:
            return {'success':False,'reason':'pose-index-no-candidates',
                'diagnostics':{'poseIndexProvider':index_diagnostic}}
        fit = solve_initial_structure(frame, product.clean_mask, product.validation_mask, route, profile,
            cancelled=cancelled,_context=context,
            wall_correspondence_identity=True,pose_index_candidates=indexed_candidates,
            pose_index_diagnostics=index_diagnostic,**static_channels)
        fit.setdefault('diagnostics',{}).setdefault('poseIndexProvider',index_diagnostic)
        if fit.get('success'):
            if fit.get('coordinateSpace') != 'author-render-to-client' or fit.get('routeProfileId') != profile:
                return {'success': False, 'reason': 'initial-structure-coordinate-mismatch'}
            fit = dict(fit)
            if not isinstance(fit.get('selectedRouteCurrentFrameResidual'), dict):
                fit['selectedRouteCurrentFrameResidual'] = self._rendered_profile_current_frame_residual(
                    frame, route, fit.get('matrixSourceToClient'), profile_id=profile)
            if not rendered_profile_residual_is_verified(fit['selectedRouteCurrentFrameResidual']):
                fit.update(success=False, reason='initial-structure-author-unverified')
        return fit
