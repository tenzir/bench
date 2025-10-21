{ sources ? import ./npins
, pkgs ? import sources.nixpkgs {}
}:

let
  python = pkgs.python313.withPackages (ps : [
    ps.uv
    ps.uv-build
  ]);
in
pkgs.mkShell {
  packages = [
    python
    pkgs.uv
    pkgs.ruff
    pkgs.basedpyright
  ];

  shellHook = ''
    export UV_NO_PROGRESS=1
    export UV_PYTHON=${python.interpreter}
  '';
}
