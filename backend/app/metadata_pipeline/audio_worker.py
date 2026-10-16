"""Optional Essentia subprocess: no media writes and no network requests."""
import json
import math
import sys


def main():
    import essentia
    import essentia.standard as es
    essentia.log.infoActive = False
    features, _ = es.MusicExtractor(lowlevelStats=['mean'], rhythmStats=['mean'],
                                    tonalStats=['mean'], rhythmMethod='multifeature')(sys.argv[1])
    values = {}
    # Only scalar descriptors are retained, never waveforms or per-frame arrays.
    for name in features.descriptorNames():
        value = features[name]
        if isinstance(value, (str, int, float)):
            values[name] = value
        elif getattr(value, 'ndim', None) == 0:
            values[name] = value.item()
    values = {name: value for name, value in values.items()
              if not isinstance(value, float) or math.isfinite(value)}
    print(json.dumps(values, allow_nan=False))


if __name__ == '__main__':
    main()
