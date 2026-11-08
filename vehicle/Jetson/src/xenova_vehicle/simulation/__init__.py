"""합성 관측의 독립 producer/export. GT·제어 권한·실물 장치 연결을 제공하지 않는다."""

from .scene import Scene, SceneSnapshot, demo_scene, oracle
from .capture import DepthEffects, SyntheticCamera
from .dataset import SyntheticDatasetError, SyntheticDatasetReader, SyntheticDatasetWriter

__all__ = ['Scene', 'SceneSnapshot', 'demo_scene', 'oracle', 'DepthEffects', 'SyntheticCamera',
           'SyntheticDatasetError', 'SyntheticDatasetReader', 'SyntheticDatasetWriter']
