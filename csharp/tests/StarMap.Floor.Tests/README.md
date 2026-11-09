# Current client map and floor observation replay

Generate the oracle from the same checkout that owns the game readers. The capture argument is the original current full-client GDI event with its bound PNG. Its timestamp, dimensions and image-scope declaration are checked. The fixture directory must be empty or new.

```powershell
python -B reference_oracle.py --repo <checkout> --capture-record <capture.json> --output <new-oracle-dir>
dotnet build-server shutdown
dotnet build
dotnet run --no-build -- --oracle <new-oracle-dir>/index.json --output <new-replay-dir>
```

The Python oracle calls `LiveRuntime._observe` with explicit season, mode and observation capability. The small host discards history and diagnostic writes. The capture and all newly drawn fixtures use the original control readers, map-open checks and floor binding. The C# runner decodes the same BGR pixels, calls `FloorObservation.Observe`, writes its result, then opens the independent expected JSON and records every field difference.

The recorded default is S0 nightmare. The S1 and difficult examples supplied by the generator are newly drawn mathematical fixtures. Only a current full client capture establishes coverage of that HUD in the running game. Pre-2026-11-05 screenshots cannot establish current controls. No map identity, pose, input action or live preparation timing is tested here.
