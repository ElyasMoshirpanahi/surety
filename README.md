# surety

A statistical guarantee layer for System One decision models.

Decision models such as Jev (TypeSafe AI) and Laya (Convai Innovations) return
typed, probabilistic decisions. Both vendors tell you to validate your
confidence thresholds on your own labelled data, but nothing gives you a
guarantee. `surety` does. From human-labelled real traffic it certifies a
confidence threshold per question, model and slice so that, with probability
at least 1 − δ, the error rate among automated decisions is at most α. At
runtime it gates decisions and fails closed to human review. It watches for
drift with an anytime-valid detector and records evidence in a hash-chained
ledger. It works with any backend that speaks the `/v1/systemone` protocol.

## Status

Early development. Nothing is released yet.

## License

Apache-2.0.

This project is not affiliated with TypeSafe AI or Convai Innovations.
