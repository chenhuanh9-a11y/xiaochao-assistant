"""Local contour retrieval is ordering only. No ID exclusion or pose authority."""
import json,math,hashlib
from collections import Counter
import numpy as np
SPEC=dict(version=1,angleBins=8,ratioBinsPerOctave=2,ratioClipOctaves=6,cornerWeight=1.,tripleWeight=2.,idf='1+ln((N+1)/(df+1))',score='TF-IDF cosine; maximum across source-independent visible components',contour='unchanged single_map_structure.contour_graph; valid nodes only',componentOverload='over 32 observed components: neutral ranking; retain entire catalog',tieBreak='original exact-index order',history='explicit prior-supported IDs first, local score second; never inferred from case name',scope='order only; no pruning; no identity or display authority')
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':')).encode()
def digest(x):return hashlib.sha256(x).hexdigest()
def node_token(node):
 rays=np.asarray(node['rays']);lengths=np.asarray(node['lengths'])
 angles=tuple(int(np.rint(np.arctan2(v[1],v[0])*SPEC['angleBins']/(2*np.pi)))%SPEC['angleBins'] for v in rays)
 ratio=int(np.rint(np.clip(np.log2(lengths[0]/lengths[1]),-SPEC['ratioClipOctaves'],SPEC['ratioClipOctaves'])*SPEC['ratioBinsPerOctave']))
 return (*angles,ratio)
def tokens(nodes):
 out=Counter();valid={i:node_token(n) for i,n in enumerate(nodes) if n['valid']}
 for i,t in valid.items():
  out['C:'+','.join(map(str,t))]+=SPEC['cornerWeight']
  prev,nxt=nodes[i]['previous'],nodes[i]['next']
  if prev in valid and nxt in valid:
   out['T:'+','.join(map(str,valid[prev]+t+valid[nxt]))]+=SPEC['tripleWeight']
 return dict(out)
def compile_vectors(records):
 df=Counter(k for row in records.values() for k in row);n=len(records);idf={k:1+math.log((n+1)/(v+1)) for k,v in df.items()};vectors={}
 for mid,row in records.items():
  vector={k:v*idf[k] for k,v in row.items()};vectors[mid]=dict(vector=vector,norm=math.sqrt(sum(v*v for v in vector.values())))
 return dict(idf=idf,vectors=vectors)
def rank(catalog,groups,query_tokens,prior_supported=()):
 scores={m:0. for g in groups for m in g['members']}
 for query in query_tokens:
  q={k:v*catalog['idf'].get(k,1.) for k,v in query.items()};qn=math.sqrt(sum(v*v for v in q.values()))
  if not qn:continue
  for mid,v in catalog['vectors'].items():
   if mid not in scores:continue
   denominator=qn*v['norm'];score=sum(value*v['vector'].get(k,0.) for k,value in q.items())/denominator if denominator else 0.
   scores[mid]=max(scores[mid],score)
 history=set(prior_supported)
 ordered=sorted(groups,key=lambda g:(not bool(history.intersection(g['members'])),-max(scores[m] for m in g['members'])))
 return ordered,scores

def observation_tokens(frame):
 from nightmare_candidate import visible_map_components
 from single_map_structure import current_geometry,contour_graph
 h,w=frame.shape[:2];components=visible_map_components(frame,observation_bounds=(0,0,w,h))
 if len(components)>32:return [],dict(componentCount=len(components),overload=True,tokenCount=0)
 output=[]
 for visible in components:
  context=current_geometry(frame,observation_bounds=(0,0,w,h),visible_override=visible)
  if context is not None:output.append(tokens(contour_graph(context['query'])))
 return output,dict(componentCount=len(components),overload=False,tokenCount=sum(len(t) for t in output))
