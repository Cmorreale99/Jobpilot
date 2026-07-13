# Architecture

The evidence spine is capture -> structure -> assign -> extract -> review -> publish.
Each stage derives from the immutable raw layer, so every downstream artifact is a
recomputable derivation. Publication is atomic: a failed candidate never modifies the
last valid Master CV version.
