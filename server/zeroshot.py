"""Lazy OWLv2 inference for the server and independent baseline evaluation.

Public contract: detect(PIL.Image) -> [{label, conf, box}], with normalized xyxy.
No model download or torch import happens when this module is imported.
"""
from functools import lru_cache
import os
from threading import Lock
from time import perf_counter

MODEL_ID = 'google/owlv2-base-patch16-ensemble'
PERSON_PROMPTS = ('person', 'person lying down', 'person in water', 'partially visible person')
RESCUE_PROMPTS = PERSON_PROMPTS + ('human hand', 'human arm')


def normalized_detections(boxes, scores, width, height):
    """Map all human queries to person, clip coordinates, and omit invalid boxes."""
    import math
    result = []
    for box, score in zip(boxes, scores, strict=True):
        values = [float(v) for v in box]
        score = float(score)
        if len(values) != 4 or not all(math.isfinite(v) for v in values + [score]):
            continue
        x1, y1, x2, y2 = values
        box = [max(0., min(1., x1 / width)), max(0., min(1., y1 / height)),
               max(0., min(1., x2 / width)), max(0., min(1., y2 / height))]
        if box[2] > box[0] and box[3] > box[1] and 0 <= score <= 1:
            result.append({'label': 'person', 'conf': score, 'box': box})
    return result


class OwlV2Detector:
    def __init__(self, *, device='cpu', prompts=PERSON_PROMPTS, threshold=0.001,
                 iou=0.7, imgsz=640, max_det=300, model_id=MODEL_ID):
        if not prompts or not all(isinstance(p, str) and p.strip() for p in prompts):
            raise ValueError('Provide nonempty human text prompts.')
        if not 0 <= threshold <= 1 or not 0 <= iou <= 1:
            raise ValueError('Threshold and IoU must be in [0, 1].')
        if imgsz <= 0 or imgsz % 16 or max_det <= 0:
            raise ValueError('Image size must be a positive multiple of 16; max_det must be positive.')
        self.device = 'cuda:' + device if device.isdigit() else device
        self.prompts = tuple(prompts)
        self.threshold, self.iou = threshold, iou
        self.imgsz, self.max_det, self.model_id = imgsz, max_det, model_id
        self.model = self.processor = None
        self._load_lock = Lock()

    def load(self):
        with self._load_lock:
            if self.model is not None:
                return
            from transformers import Owlv2ForObjectDetection, Owlv2Processor
            processor = Owlv2Processor.from_pretrained(self.model_id, use_fast=False)
            processor.image_processor.size = {'height': self.imgsz, 'width': self.imgsz}
            model = Owlv2ForObjectDetection.from_pretrained(self.model_id).to(self.device).eval()
            self.processor, self.model = processor, model

    def _sync(self):
        import torch
        if self.device.startswith('cuda'):
            torch.cuda.synchronize(self.device)
        elif self.device == 'mps':
            torch.mps.synchronize()

    def predict(self, image):
        import torch
        from torchvision.ops import nms
        self.load()
        image = image.convert('RGB')
        self._sync()
        start = perf_counter()
        inputs = self.processor(text=[list(self.prompts)], images=image,
                                return_tensors='pt').to(self.device)
        self._sync()
        prepared = perf_counter()
        with torch.inference_mode():
            # Pretrained positions are interpolated to the explicitly recorded 640px size.
            outputs = self.model(**inputs, interpolate_pos_encoding=True)
        self._sync()
        inferred = perf_counter()
        result = self.processor.post_process_object_detection(
            outputs, threshold=self.threshold,
            target_sizes=torch.tensor([[image.height, image.width]], device=self.device))[0]
        boxes, scores = result['boxes'], result['scores']
        # All queries describe the same target class: use class-agnostic NMS.
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clamp(0, image.width)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clamp(0, image.height)
        valid = torch.isfinite(boxes).all(1) & torch.isfinite(scores)
        valid &= (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])
        boxes, scores = boxes[valid], scores[valid]
        keep = nms(boxes, scores, self.iou)[:self.max_det]
        detections = normalized_detections(boxes[keep].cpu().tolist(), scores[keep].cpu().tolist(),
                                           image.width, image.height)
        self._sync()
        end = perf_counter()
        return detections, {'preprocess': (prepared-start)*1000,
                            'inference': (inferred-prepared)*1000,
                            'postprocess': (end-inferred)*1000}


@lru_cache(maxsize=1)
def _server_detector():
    import torch
    device = os.getenv('DEVICE', '').strip()
    if not device:
        device = 'cuda' if torch.cuda.is_available() else ('mps' if torch.backends.mps.is_available() else 'cpu')
    return OwlV2Detector(device=device, prompts=RESCUE_PROMPTS,
                         threshold=float(os.getenv('ZEROSHOT_CONF', '0.1')),
                         imgsz=int(os.getenv('ZEROSHOT_IMGSZ', '640')))


def detect(image):
    """Server entry point; hand/arm matches are possible-person alerts, not full boxes."""
    return _server_detector().predict(image)[0]
