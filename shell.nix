# Backward compatibility for nix-shell users
(import (
  fetchTarball {
    url = "https://github.com/edolstra/flake-compat/archive/master.tar.gz";
    sha256 = "0pf91b1hx9ky8anj3j3v1fvlvg71l2pxw7c2r9p7hi4v5xz1hfs5";
  }
) {
  src = ./.;
}).shellNix
