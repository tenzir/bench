{ sources ? import ./npins
, pkgs ? import sources.nixpkgs {}
}:

let
  python = pkgs.python313;
in
pkgs.mkShell {
  packages = with pkgs; [
    python
    uv
    ruff
    basedpyright
  ];

  shellHook = ''
    export UV_NO_PROGRESS=1
    export UV_PYTHON=${python.interpreter}
  '';
}
