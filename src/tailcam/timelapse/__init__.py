"""Timelapse capture + encoding.

Captures sparse frames over a long period (tailored for 3D prints), keeps the
raw frames on disk, and encodes them into a video. Keeping the source frames is
deliberate: a later phase can re-stitch them with frame interpolation/deflicker
into smooth, flowing motion.
"""
