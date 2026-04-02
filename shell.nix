{ sources ? import ./npins
, pkgs ? import sources.nixpkgs {}
, treefmt-nix ? import sources.treefmt-nix
}:

let
  python = pkgs.python313.withPackages (ps : [
    ps.boto3
    ps.click
    ps.platformdirs
    ps.pygithub
    ps.pyyaml
    ps.uv
    ps.uv-build
  ]);

  treefmt = treefmt-nix.mkWrapper pkgs {
    # Used to find the project root
    projectRootFile = ".git/config";
    programs.mdformat.enable = true;
    programs.nixfmt.enable = true;
    programs.ruff-check.enable = true;
    programs.ruff-format.enable = true;
    programs.yamlfmt.enable = true;
    programs.yamllint.enable = true;
  };
in
pkgs.mkShell {
  packages = [
    python
    pkgs.uv
    pkgs.ruff
    pkgs.basedpyright
    treefmt
  ];

  shellHook = ''
    export UV_NO_PROGRESS=1
    export UV_PYTHON=${python.interpreter}
  '';
}
