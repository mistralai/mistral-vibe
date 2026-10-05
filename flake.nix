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

    # Crane builds the Rust CLI as its own cached derivation and vendors its
    # crate tree from the lockfile, so no hash has to be maintained for it.
    crane.url = "github:ipetkov/crane";
  };

  outputs = {
    self,
    nixpkgs,
    flake-utils,
    uv2nix,
    pyproject-nix,
    pyproject-build-systems,
    crane,
    ...
  }:
    flake-utils.lib.eachDefaultSystem (system: let
      inherit (nixpkgs) lib;

      workspace = uv2nix.lib.workspace.loadWorkspace {workspaceRoot = ./.;};

      overlay = workspace.mkPyprojectOverlay {
        sourcePreference = "wheel"; # sdist if you want
      };

      pyprojectOverrides = final: prev: {
        # NOTE: If a package complains about a missing dependency (such
        # as setuptools), you can add it here.
        untokenize = prev.untokenize.overrideAttrs (old: {
          buildInputs = (old.buildInputs or []) ++ final.resolveBuildSystem {setuptools = [];};
        });

        # cryptography 50.0.0 has no macOS x86_64 wheel. Source builds need
        # the Python backend and vendored Rust crates inside the Nix sandbox.
        cryptography = prev.cryptography.overrideAttrs (old:
          lib.optionalAttrs (old.passthru.format == "pyproject") {
            cargoDeps = pkgs.rustPlatform.fetchCargoVendor {
              inherit (old) pname version src;
              # Matches cryptography 50.0.0 in nixpkgs (a7e1a760ab81).
              hash = "sha256-heJGLh0MgDPpksWyPLaIkZ5gVEWx8UnaJKv4GvclpmI=";
            };
            buildInputs = (old.buildInputs or [])
              ++ [pkgs.openssl]
              ++ lib.optionals pkgs.stdenv.isDarwin [pkgs.libiconv];
            nativeBuildInputs = (old.nativeBuildInputs or [])
              ++ final.resolveBuildSystem {
                maturin = [];
                cffi = [];
                setuptools = [];
              }
              ++ [
                pkgs.rustPlatform.cargoSetupHook
                pkgs.cargo
                pkgs.rustc
                pkgs.pkg-config
              ];
          });

        # The wheel override applies only where the Harness lockfile at
        # harness/core is reachable: trees that ship it build the wheel;
        # trees that do not never build it, so the derivation stays plain
        # there and keeps evaluating.
        mistral-vibe =
          if builtins.pathExists ./harness/core/Cargo.lock
          then
            prev.mistral-vibe.overrideAttrs (old: let
              # The v8 crate's build script downloads a prebuilt librusty_v8
              # archive at build time; the sandbox has no network, so fetch it
              # here and point RUSTY_V8_ARCHIVE at it (the script accepts local
              # paths). Hashes match v8 150.3.0, pinned by harness/core's
              # Cargo.lock; suffixes match the features the Harness enables.
              # Upstream release assets, so unlike the crate vendoring below
              # they cannot be derived from the lock; a pin check catches
              # the bump that makes them stale.
              rustyV8Archive = pkgs.fetchurl {
                url = "https://github.com/denoland/rusty_v8/releases/download/v150.3.0/librusty_v8_simdutf_release_${pkgs.stdenv.hostPlatform.rust.rustcTarget}.a.gz";
                hash = {
                  "x86_64-linux" = "sha256-2wl+bvpVp14L3oayuhl5g9CpG76DAfRVDEkNecB9evA=";
                  "aarch64-linux" = "sha256-WR80+czoM+IAeYduyM4gsR0Y6jGk9BL0u6EQGB3ZdrY=";
                  "x86_64-darwin" = "sha256-fs7glB27R0inqhS4+PufsVLUhj09NCLOshbnRBc+5mI=";
                  "aarch64-darwin" = "sha256-UfWFt8OGkwYSn/1haKjwuJqNSjhw3LBwlgwrT9fQSCU=";
                }.${pkgs.stdenv.hostPlatform.system} or (throw "librusty_v8 prebuilt archive not pinned for ${pkgs.stdenv.hostPlatform.system}");
              };
              # Vendored Harness crates for the Maturin build, derived from
              # the lockfile so harness dependency bumps never need a vendor
              # hash update here. crane's layout does not satisfy
              # cargoSetupHook's contract (it ships no lockfile to diff and
              # puts config.toml at the output root), so wire cargo at the
              # config level instead: cargo reads .cargo/config.toml from
              # the source root upward, which also covers the harness/core
              # workspace maturin builds from.
              harnessVendor = craneLib.vendorCargoDeps {
                cargoLock = ./harness/core/Cargo.lock;
              };
            in {
              env = (old.env or {}) // {
                VIBE_SKIP_RUST_TUI = "1";
                # Without cargo on PATH, Maturin would try to rustup-install a
                # toolchain; fail with a clear error instead.
                MATURIN_NO_INSTALL_RUST = "1";
                RUSTY_V8_ARCHIVE = "${rustyV8Archive}";
              };
              # The Rust CLI is built by crane above and lands in the wheel
              # through its vibe/_bin include; VIBE_SKIP_RUST_TUI keeps the
              # backend from trying to rebuild it inside this derivation.
              prePatch = (old.prePatch or "") + ''
                mkdir -p vibe/_bin
                cp ${rustCli}/bin/vibe-rs vibe/_bin/vibe-rs
              '';

              # Nix builds the workspace tree, not an unpacked sdist, but the
              # backend's sdist path is the right one in the sandbox: it skips
              # the manylinux compatibility tag and zig cross-compilation,
              # which only matter for portable wheels uploaded out of Nix.
              # The backend detects an sdist by a PKG-INFO at the project
              # root.
              postPatch = (old.postPatch or "") + "\ntouch PKG-INFO";
              preBuild = (old.preBuild or "") + ''
                mkdir -p .cargo
                cat ${harnessVendor}/config.toml >> .cargo/config.toml
              '';
              nativeBuildInputs = (old.nativeBuildInputs or [])
                ++ [
                  pkgs.cargo
                  pkgs.rustc
                ];
            })
          else prev.mistral-vibe;
      };

      pkgs = import nixpkgs {
        inherit system;
      };

      python = pkgs.python312;

      # Crane builds the Rust CLI (vibe-rs) as its own derivation, so editing
      # its sources does not rebuild the Python wheel, and vice versa.
      craneLib = crane.mkLib pkgs;

      # crane expects the crate at the source root, but banners.rs
      # include_str!s whats_new.md from above the cli-rust crate. Flatten
      # the crate to the source root and move the file in beside it.
      rustCliSrc = pkgs.runCommand "vibe-rs-src" {} ''
        cp -r ${./vibe/cli-rust} $out
        chmod -R +w $out
        cp ${./vibe/whats_new.md} $out/whats_new.md
        substituteInPlace $out/src/startup/banners.rs \
          --replace-fail '../../../whats_new.md' '../../whats_new.md'
      '';

      rustCli = craneLib.buildPackage {
        src = rustCliSrc;
        cargoVendorDir = craneLib.vendorCargoDeps {
          cargoLock = ./vibe/cli-rust/Cargo.lock;
        };
        # The default `voice` feature pulls in cpal: its Linux backend links
        # ALSA via pkg-config, and its macOS backend (coreaudio-sys) runs
        # bindgen, which needs libclang. The darwin stdenv SDK resolves the
        # CoreAudio framework on its own.
        buildInputs = lib.optionals pkgs.stdenv.isLinux [pkgs.alsa-lib];
        nativeBuildInputs = [pkgs.pkg-config pkgs.libclang];
        env = {LIBCLANG_PATH = "${lib.getLib pkgs.libclang}/lib";};
        cargoExtraArgs = "--locked";
        # The wheel only bundles the binary; tests run in CI, not here.
        doCheck = false;
      };

      # Construct package set
      pythonSet =
        # Use base package set from pyproject.nix builders
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
      inherit (pkgs.callPackages pyproject-nix.build.util { }) mkApplication;
    in {

      packages = {
        default = mkApplication {
          venv = pythonSet.mkVirtualEnv "mistralai-vibe-env" workspace.deps.default;
          package = pythonSet.mistral-vibe;
        };

        # Convenience alias: the full application with the Rust TUI forced
        # via VIBE_CLI, so `nix build` and `nix run` both accept .#rustCli.
        rustCli = pkgs.writeShellScriptBin "vibe" ''
          export VIBE_CLI=rust
          exec "${self.packages.${system}.default}/bin/vibe" "$@"
        '';
      };

      apps = {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/vibe";
        };

        # Same application, but the launcher picks the Rust TUI instead of
        # the Python one.
        rustCli = {
          type = "app";
          program = "${self.packages.${system}.rustCli}/bin/vibe";
        };
      };

      devShells = {
        default = let
          editableOverlay = workspace.mkEditablePyprojectOverlay {
            root = "$REPO_ROOT";
          };

          editablePythonSet = pythonSet.overrideScope (
            lib.composeManyExtensions [
              editableOverlay

              # Apply fixups for building an editable package of your workspace packages
              (final: prev: {
                mistralai-vibe = prev.mistralai-vibe.overrideAttrs (old: {
                  # It's a good idea to filter the sources going into an editable build
                  # so the editable package doesn't have to be rebuilt on every change.
                  src = lib.fileset.toSource {
                    root = old.src;
                    fileset = lib.fileset.unions [
                      (old.src + "/pyproject.toml")
                      (old.src + "/README.md")
                    ];
                  };

                  nativeBuildInputs =
                    old.nativeBuildInputs
                    ++ final.resolveBuildSystem {
                      editables = [];
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
              # Don't create venv using uv
              UV_NO_SYNC = "1";

              # Force uv to use Python interpreter from venv
              UV_PYTHON = "${virtualenv}/bin/python";

              # Prevent uv from downloading managed Python's
              UV_PYTHON_DOWNLOADS = "never";
            };

            shellHook = ''
              # Undo dependency propagation by nixpkgs.
              unset PYTHONPATH

              # Get repository root using git. This is expanded at runtime by the editable `.pth` machinery.
              export REPO_ROOT=$(git rev-parse --show-toplevel)
            '';
          };
      };
    });
}
