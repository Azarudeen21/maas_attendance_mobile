# Bundled face models

Pinned from opencv/opencv_zoo commit 48b10fa505aeb51fbd127fc60a5c660040e823b7.
`faces.py` verifies these SHA-256 values before loading either model.

| File | Purpose | License | SHA-256 |
| --- | --- | --- | --- |
| face_detection_yunet_2023mar.onnx | Face detection and landmarks (YuNet) | MIT, see LICENSE-YuNet-MIT.txt | 8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4 |
| face_recognition_sface_2021dec.onnx | 128-value face embedding (SFace) | Apache-2.0, see LICENSE-SFace-Apache-2.0.txt | 0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79 |

Replacing a model changes `MODEL_VERSION`; existing enrollments must be re-enrolled.
