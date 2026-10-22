"""Conservative foot-pinned endpoint width/height matching, not skeletal morphing."""
import cv2
import numpy as np

def dimensions(a,pivot):
 mask=a[:,:,3]>64
 ys,xs=np.nonzero(mask)
 if len(xs)<100:return None
 h=pivot[1]-np.quantile(ys,.01)
 band=(ys>pivot[1]-.75*h)&(ys<pivot[1]-.25*h)&(abs(xs-pivot[0])<.35*h)
 if band.sum()<100:return None
 return np.array([np.quantile(xs[band],.95)-np.quantile(xs[band],.05),h])

def warp(a,pivot,scales):
 sx,sy=scales;x,y=pivot
 m=np.array([[sx,0,x*(1-sx)],[0,sy,y*(1-sy)]],dtype=np.float32)
 return cv2.warpAffine(a,m,(a.shape[1],a.shape[0]),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)

def overlap(a,b):
 a=a[:,:,3]>64;b=b[:,:,3]>64
 return float((a&b).sum()/max(1,(a|b).sum()))

def fit(previous,incoming,pivot):
 a=dimensions(previous,pivot);b=dimensions(incoming,pivot)
 if a is None or b is None:return np.ones(2),{}
 scales=np.clip(a/np.maximum(b,1),.90,1.10)
 candidate=warp(incoming,pivot,scales)
 before=overlap(previous,incoming);after=overlap(previous,candidate)
 # Reject harmful changes; retain RGBA premultiplication through linear sampling.
 if after<before+.002:scales=np.ones(2);after=before
 return scales,dict(scale=scales.tolist(),iou_before=round(before,4),iou_after=round(after,4))
