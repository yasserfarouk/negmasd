#!/bin/sh
# Regenerate the checked-in protobuf stubs from negmasd.proto.
#
# Needs the dev group (grpcio-tools); the generated files are committed so that
# *running* negmasd over gRPC needs only the `grpc` extra, never the compiler.
set -e
cd "$(dirname "$0")"
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. negmasd.proto
# protoc emits a top-level `import negmasd_pb2`, which does not resolve from
# inside a package; rewrite it to a relative import.
sed -i '' 's/^import negmasd_pb2 as negmasd__pb2$/from . import negmasd_pb2 as negmasd__pb2  # patched: relative import for package use/' negmasd_pb2_grpc.py
echo "regenerated negmasd_pb2.py and negmasd_pb2_grpc.py"
