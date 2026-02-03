{
  description = "Mistral Vibe!";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      uv2nix,
      pyproject-nix,
      pyproject-build-systems,
      ...
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        inherit (nixpkgs) lib;
        pkgs = import nixpkgs { inherit system; };

        # Load workspace
        workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

        # Create overlay for dependencies
        overlay = workspace.mkPyprojectOverlay {
          sourcePreference = "wheel"; # sdist if you want
        };

        # Manual overrides for specific packages
        pyprojectOverrides = final: prev: {
          # NOTE: If a package complains about a missing dependency (such
          # as setuptools), you can add it here.
          untokenize = prev.untokenize.overrideAttrs (old: {
            buildInputs = (old.buildInputs or [ ]) ++ final.resolveBuildSystem { setuptools = [ ]; };
          });
        };

        python = pkgs.python312;

        # Construct package set
        pythonSet =
          (pkgs.callPackage pyproject-nix.build.packages {
            inherit python;
          }).overrideScope
            (
              lib.composeManyExtensions [
                pyproject-build-systems.overlays.default
                overlay
                pyprojectOverrides
              ]
            );
      in
      {
        packages.default =
          let
            vibe-env = pythonSet.mkVirtualEnv "mistralai-vibe-env" workspace.deps.default;
          in
          pkgs.writeShellScriptBin "vibe" ''
            exec ${vibe-env}/bin/vibe "$@"
          '';

        apps.default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/vibe";
        };

        devShells.default =
          let
            # Create an overlay for editable development
            editableOverlay = workspace.mkEditablePyprojectOverlay {
              root = "$REPO_ROOT";
            };

            editablePythonSet = pythonSet.overrideScope (
              lib.composeManyExtensions [
                editableOverlay
                # Apply fixups for building an editable package of your workspace packages
                (final: prev: {
                  mistralai-vibe = prev.mistralai-vibe.overrideAttrs (old: {
                    src = lib.fileset.toSource {
                      root = old.src;
                      fileset = lib.fileset.unions [
                        (old.src + "/pyproject.toml")
                        (old.src + "/README.md")
                      ];
                    };
                    nativeBuildInputs =
                      (old.nativeBuildInputs or [ ])
                      ++ final.resolveBuildSystem {
                        editables = [ ];
                      };
                  });
                })
              ]
            );

            virtualenv = editablePythonSet.mkVirtualEnv "mistralai-vibe-dev-env" workspace.deps.all;
          in
          pkgs.mkShell {
            packages = [
              virtualenv
              pkgs.uv
            ];
            env = {
              UV_NO_SYNC = "1";
              UV_PYTHON = "${virtualenv}/bin/python";
              UV_PYTHON_DOWNLOADS = "never";
            };
            shellHook = ''
              unset PYTHONPATH
              export REPO_ROOT=$(git rev-parse --show-toplevel)
            '';
          };
      }
    );
}