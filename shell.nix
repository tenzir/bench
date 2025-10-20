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
  ];

  shellHook = ''
    export UV_NO_PROGRESS=1
  '';
}
