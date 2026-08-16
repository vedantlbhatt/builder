const { withDangerousMod } = require('expo/config-plugins');
const fs = require('fs');
const path = require('path');

/**
 * Disable `consteval` in the `fmt` library that React Native pins.
 *
 * fmt guards `FMT_STRING` behind `FMT_CONSTEVAL`, which expands to `consteval` when the
 * compiler advertises support. Xcode 26's clang advertises it and then rejects fmt's own
 * usage as "call to consteval function ... is not a constant expression", so the iOS build
 * fails with five errors in `format-inl.h` before any app code is compiled.
 *
 * This exists as a config plugin rather than as an edit to `ios/Podfile` because
 * `expo prebuild` REGENERATES that file. Patching it directly works exactly once and then
 * silently disappears the next time anyone runs prebuild or clones the repo — and the
 * failure looks like a toolchain problem rather than a lost patch.
 *
 * A preprocessor define was tried first and does not work: the header is included from
 * translation units across several pod targets, and the define does not reach all of them.
 * The guard has to be forced in the header itself.
 */
const PATCH_MARKER = 'BUILDER_FMT_PATCH';

const POST_INSTALL_HOOK = `
    # ${PATCH_MARKER}: Xcode 26 clang rejects fmt's own FMT_STRING/consteval usage.
    # See plugins/withFmtConstevalPatch.js for why this is a header edit and not a define.
    fmt_base = File.join(installer.sandbox.root, 'fmt', 'include', 'fmt', 'base.h')
    if File.exist?(fmt_base)
      contents = File.read(fmt_base)
      unless contents.include?('${PATCH_MARKER}')
        patched = contents.sub(
          "#if FMT_USE_CONSTEVAL\\n#  define FMT_CONSTEVAL consteval",
          "// ${PATCH_MARKER}\\n#if 0\\n#  define FMT_CONSTEVAL consteval"
        )
        if patched != contents
          FileUtils.chmod(0o644, fmt_base) # pod sources are checked out read-only
          File.write(fmt_base, patched)
          Pod::UI.puts '[builder] patched fmt/base.h to disable consteval'
        end
      end
    end
`;

module.exports = function withFmtConstevalPatch(config) {
  return withDangerousMod(config, [
    'ios',
    async (cfg) => {
      const podfilePath = path.join(cfg.modRequest.platformProjectRoot, 'Podfile');
      let contents = fs.readFileSync(podfilePath, 'utf8');

      if (contents.includes(PATCH_MARKER)) return cfg;

      if (!contents.startsWith("require 'fileutils'")) {
        contents = `require 'fileutils'\n${contents}`;
      }

      const marker = '  post_install do |installer|';
      const idx = contents.indexOf(marker);
      if (idx === -1) {
        throw new Error(
          'withFmtConstevalPatch: no post_install block in the generated Podfile. ' +
            'The template changed; update this plugin rather than editing ios/Podfile, ' +
            'which prebuild regenerates.'
        );
      }

      const insertAt = contents.indexOf('\n', idx) + 1;
      contents = contents.slice(0, insertAt) + POST_INSTALL_HOOK + contents.slice(insertAt);
      fs.writeFileSync(podfilePath, contents);
      return cfg;
    },
  ]);
};
