"""
qa.quality.make_patch — build and verify the proposed change to the module.

The script

1. reads ``aurcore/modules/spectral_de_noise.py``,
2. applies the proposed changes as exact string replacements (each one is
   asserted to match exactly once),
3. writes the unified diff to ``qa/quality/spectral_de_noise.patch``,
4. installs the patched module into a throw-away copy of the tree and
   measures it, so the numbers quoted in the report are the numbers the
   patch actually produces.

The module in the repository is **not** touched: the patched copy only ever
exists inside the temporary verification tree.
"""
from __future__ import annotations

import difflib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "aurcore" / "modules" / "spectral_de_noise.py"
PATCH_OUT = Path(__file__).resolve().parent / "spectral_de_noise.patch"

# ---------------------------------------------------------------------------
# The proposed changes.  Each entry is (old, new) and must match exactly once.
# ---------------------------------------------------------------------------
EDITS: list[tuple[str, str]] = [
    # -- R5: the broadband window is defined in Hz, not in bins ---------------
    (
        """#: Percentile used when a noise profile is captured from a learning block.
_LEARN_PERCENTILE = 50.0
""",
        """#: Percentile used when a noise profile is captured from a learning block.
_LEARN_PERCENTILE = 50.0

#: Width of the excluded zone and of the analysis window of the broadband
#: floor, in Hz.  A fixed number of bins would mean a different width for
#: every analysis resolution, and a zone that is too narrow lets the wanted
#: partials next to a bin pollute its noise estimate.
_BROAD_GAP_HZ = 500.0
_BROAD_SPAN_HZ = 1500.0
""",
    ),
    # -- R4/R5: the tonal decision uses the learned noise profile -------------
    (
        """        sm_db = self._smooth_magnitude(mag, state, cfg)
        broad_db = broadband_floor(sm_db)
        tonal_mask = np.clip((sm_db - broad_db) / 6.0, 0.0, 1.0)
        learned_tonal = self._learned_tonal_mask(tonal_mask.shape[-1])
        # Tonal peaks that belong to the learned noise profile are noise, not
        # wanted content: they must not be protected by the tonal law.
        blend = tonal_mask * (1.0 - learned_tonal)
        floor_db = self._noise_floor(sm_db, state, cfg, broad_db, tonal_mask, learned_tonal)

        gain_db = self._gain_curve(
            mag, sm_db, floor_db, broad_db, blend, state, cfg
        )
""",
        """        sm_db = self._smooth_magnitude(mag, state, cfg)
        broad_db = broadband_floor(
            sm_db, gap=self._broad_gap(cfg), span=self._broad_span(cfg)
        )
        learned_tonal = self._learned_tonal_mask(sm_db.shape[-1])
        # The tonal decision is made against the learned noise profile when
        # one is available: the instantaneous broadband estimate is polluted
        # by the wanted partials that sit next to it, and that pollution
        # makes the de-noiser treat wanted content as noise.
        ref_db = self._noise_reference(state, broad_db)
        tonal_mask = np.clip((sm_db - ref_db) / 6.0, 0.0, 1.0)
        # Tonal peaks that belong to the learned noise profile are noise, not
        # wanted content: they must not be protected by the tonal law.
        blend = tonal_mask * (1.0 - learned_tonal)
        floor_db = self._noise_floor(sm_db, state, cfg, ref_db, tonal_mask, learned_tonal)

        gain_db = self._gain_curve(
            mag, sm_db, floor_db, ref_db, blend, state, cfg
        )
""",
    ),
    # -- R1: the mask may not undo the noise reduction ------------------------
    (
        """        gain_db = self._apply_masking(gain_db, mag, cfg)
        gain = db_to_lin(gain_db)
""",
        """        gain_db = self._apply_masking(gain_db, mag, cfg, snr_db=sm_db - floor_db)
        gain = db_to_lin(gain_db)
""",
    ),
    # -- R4/R5: the new helpers ----------------------------------------------
    (
        """    def _learned_tonal_mask(self, bins: int) -> np.ndarray:
""",
        """    def _broad_gap(self, cfg: dict[str, Any]) -> int:
        \"\"\"Half width of the zone the broadband floor excludes, in bins.\"\"\"
        bin_hz = float(self.sample_rate) / float(cfg[\"fft\"])
        return max(2, int(round(_BROAD_GAP_HZ / bin_hz)))

    def _broad_span(self, cfg: dict[str, Any]) -> int:
        \"\"\"Half width of the broadband floor's analysis window, in bins.\"\"\"
        bin_hz = float(self.sample_rate) / float(cfg[\"fft\"])
        return max(self._broad_gap(cfg) + 4, int(round(_BROAD_SPAN_HZ / bin_hz)))

    def _noise_reference(
        self, state: _DenoiseState, broad_db: np.ndarray
    ) -> np.ndarray:
        \"\"\"The per-bin noise level the tonal decision is made against.

        The learned profile is used when it is available: it is captured
        from a noise-only block, so it is not polluted by the wanted
        content that sits next to it.  Without a profile the instantaneous
        broadband estimate is the best available reference.
        \"\"\"
        learned = state.learned_floor_db
        if learned is None:
            return broad_db
        bins = broad_db.shape[-1]
        if learned.shape[-1] != bins:
            learned = np.interp(
                np.linspace(0.0, 1.0, bins),
                np.linspace(0.0, 1.0, learned.shape[-1]),
                learned,
            )
            state.learned_floor_db = learned
        return learned[None, :]

    def _learned_tonal_mask(self, bins: int) -> np.ndarray:
""",
    ),
    # -- R4: the floor of a tonal bin follows the reference -------------------
    (
        """        cfg: dict[str, Any],
        broad_db: np.ndarray,
        tonal_mask: np.ndarray,
        learned_tonal: np.ndarray,
    ) -> np.ndarray:
        \"\"\"Per-bin broadband noise floor in dB.
""",
        """        cfg: dict[str, Any],
        ref_db: np.ndarray,
        tonal_mask: np.ndarray,
        learned_tonal: np.ndarray,
    ) -> np.ndarray:
        \"\"\"Per-bin broadband noise floor in dB.
""",
    ),
    (
        """        floor = np.maximum(broad_db, state.min_db[None, :] + cfg[\"floor_bias_db\"])
        wanted_tonal = (tonal_mask > 0.5) & (learned_tonal < 0.5)
        if np.any(wanted_tonal):
            floor = np.where(wanted_tonal, broad_db, floor)
""",
        """        floor = np.maximum(ref_db, state.min_db[None, :] + cfg[\"floor_bias_db\"])
        wanted_tonal = (tonal_mask > 0.5) & (learned_tonal < 0.5)
        if np.any(wanted_tonal):
            floor = np.where(wanted_tonal, ref_db, floor)
""",
    ),
    # -- R6: the OM-LSA estimator needs a noise *power* ------------------------
    (
        """        noise_power = np.maximum(db_to_lin(floor_db), 1e-18)
        if cfg[\"omlsa\"]:
""",
        """        # The estimator compares powers: feeding it the magnitude of the
        # floor drives gamma and xi far too low, the speech presence
        # probability collapses to zero and the branch saturates the
        # reduction instead of refining it.
        noise_power = np.maximum(db_to_lin(floor_db), 1e-18) ** 2
        if cfg[\"omlsa\"]:
""",
    ),
    # -- R4: the tonal SNR is measured against the reference -------------------
    (
        """        # Tonal components are judged against the broadband floor instead.
        tonal_snr = sm_db - broad_db
        tonal_weight = soft_weight(tonal_snr, cfg[\"tonal_threshold\"], cfg[\"knee_db\"])
        tonal_depth = np.minimum(tonal_weight * cfg[\"tonal_reduction\"], cfg[\"tonal_reduction\"])
""",
        """        # Tonal components are judged against the noise reference instead.
        tonal_snr = sm_db - ref_db
        tonal_weight = soft_weight(tonal_snr, cfg[\"tonal_threshold\"], cfg[\"knee_db\"])
        tonal_depth = np.minimum(tonal_weight * cfg[\"tonal_reduction\"], cfg[\"tonal_reduction\"])
""",
    ),
    (
        """    def _gain_curve(
        self,
        mag: np.ndarray,
        sm_db: np.ndarray,
        floor_db: np.ndarray,
        broad_db: np.ndarray,
        blend: np.ndarray,
        state: _DenoiseState,
        cfg: dict[str, Any],
    ) -> np.ndarray:
""",
        """    def _gain_curve(
        self,
        mag: np.ndarray,
        sm_db: np.ndarray,
        floor_db: np.ndarray,
        ref_db: np.ndarray,
        blend: np.ndarray,
        state: _DenoiseState,
        cfg: dict[str, Any],
    ) -> np.ndarray:
""",
    ),
    # -- R3: the temporal smoothing engages on the first block ----------------
    (
        """        if state.prev_gain_db is None or state.prev_gain_db.shape != (n_bins,):
            state.prev_gain_db = gain_db[-1].copy()
            return gain_db
""",
        """        if state.prev_gain_db is None or state.prev_gain_db.shape != (n_bins,):
            # The smoothing starts with the first block: frame 0 keeps its
            # raw value, the rest is smoothed against the running state.
            # Returning the raw curve (as before) meant that the
            # musical-noise control did nothing at all for a render that
            # arrives in one piece.
            current = gain_db[0].astype(np.float64, copy=True)
            out = gain_db.copy()
            for i in range(1, n_frames):
                target = gain_db[i]
                alpha = np.where(target > current, alpha_rel, alpha_att)
                current = current + alpha * (target - current)
                out[i] = current
            state.prev_gain_db = out[-1].copy()
            return out
""",
    ),
    # -- R4/R5: the extra resolutions use the same reference -------------------
    (
        """            gain_db = self._multi_resolution_gain(
                segment, gain_db, floor_db, freqs, blend, cfg
            )
""",
        """            gain_db = self._multi_resolution_gain(
                segment, gain_db, floor_db, ref_db, freqs, blend, cfg
            )
""",
    ),
    (
        """        floor_db: np.ndarray,
        freqs: np.ndarray,
        blend: np.ndarray,
        cfg: dict[str, Any],
    ) -> np.ndarray:
        \"\"\"Combine the gain curves of several window sizes (most conservative wins).
""",
        """        floor_db: np.ndarray,
        ref_db: np.ndarray,
        freqs: np.ndarray,
        blend: np.ndarray,
        cfg: dict[str, Any],
    ) -> np.ndarray:
        \"\"\"Combine the gain curves of several window sizes (most conservative wins).
""",
    ),
    (
        """            sm_db = _moving_average(20.0 * np.log10(np.maximum(mag, 1e-12)), 3, axis=0)
            extra_floor = np.interp(extra_freqs, freqs, floor_db[-1])[None, :]
            broad = broadband_floor(sm_db)
            tonal = np.clip((sm_db - broad) / 6.0, 0.0, 1.0)
            learned = self._learned_tonal_mask(extra_freqs.size)
            extra_blend = tonal * (1.0 - learned[None, :])
            extra_gain = self._gain_curve_static(
                mag, sm_db, extra_floor, broad, extra_blend, cfg
            )
""",
        """            sm_db = _moving_average(20.0 * np.log10(np.maximum(mag, 1e-12)), 3, axis=0)
            extra_floor = np.interp(extra_freqs, freqs, floor_db[-1])[None, :]
            broad = broadband_floor(
                sm_db, gap=self._broad_gap(cfg), span=self._broad_span(cfg)
            )
            ref = np.interp(extra_freqs, freqs, ref_db[-1])[None, :]
            tonal = np.clip((sm_db - ref) / 6.0, 0.0, 1.0)
            learned = self._learned_tonal_mask(extra_freqs.size)
            extra_blend = tonal * (1.0 - learned[None, :])
            extra_gain = self._gain_curve_static(
                mag, sm_db, extra_floor, ref, extra_blend, cfg
            )
""",
    ),
    (
        """        floor_db: np.ndarray,
        broad_db: np.ndarray,
        blend: np.ndarray,
        cfg: dict[str, Any],
    ) -> np.ndarray:
        \"\"\"Gain curve without the temporal state (used by the extra resolutions).\"\"\"
""",
        """        floor_db: np.ndarray,
        ref_db: np.ndarray,
        blend: np.ndarray,
        cfg: dict[str, Any],
    ) -> np.ndarray:
        \"\"\"Gain curve without the temporal state (used by the extra resolutions).\"\"\"
""",
    ),
    (
        """        tonal_snr = sm_db - broad_db
        tonal_weight = soft_weight(tonal_snr, cfg[\"tonal_threshold\"], cfg[\"knee_db\"])
        tonal_gain_db = -np.minimum(
""",
        """        tonal_snr = sm_db - ref_db
        tonal_weight = soft_weight(tonal_snr, cfg[\"tonal_threshold\"], cfg[\"knee_db\"])
        tonal_gain_db = -np.minimum(
""",
    ),
    # -- R1: the masking limit only applies to wanted content -----------------
    (
        """    def _apply_masking(
        self, gain_db: np.ndarray, mag: np.ndarray, cfg: dict[str, Any]
    ) -> np.ndarray:
        \"\"\"Limit the attenuation with a psychoacoustic masking model.\"\"\"
        amount = float(cfg[\"masking\"])
        if amount <= 0.0:
            return gain_db
        mag_db = 20.0 * np.log10(np.maximum(mag, 1e-12))
        offset = -(14.0 + 26.0 * (1.0 - amount))
        try:
            mask_db = _spectral.masking_model(
                mag_db, offset, int(self.sample_rate), int(cfg[\"fft\"])
            )
        except Exception:
            return gain_db
        limit = mask_db - mag_db
        # The mask may only *relax* the attenuation, never amplify a bin.
        limit = np.minimum(limit, 0.0)
        return gain_db + amount * np.maximum(0.0, limit - gain_db)
""",
        """    def _apply_masking(
        self,
        gain_db: np.ndarray,
        mag: np.ndarray,
        cfg: dict[str, Any],
        snr_db: np.ndarray | None = None,
    ) -> np.ndarray:
        \"\"\"Limit the attenuation with a psychoacoustic masking model.

        The model protects wanted content from being masked by its
        neighbours.  It must therefore only relax the attenuation of a bin
        that carries wanted content: applied to a bin that is pure noise it
        silently undoes the noise reduction, and because the mask follows
        the instantaneous spectrum it also makes the gain curve breathe -
        which is exactly what musical noise is.
        \"\"\"
        amount = float(cfg[\"masking\"])
        if amount <= 0.0:
            return gain_db
        mag_db = 20.0 * np.log10(np.maximum(mag, 1e-12))
        offset = -(14.0 + 26.0 * (1.0 - amount))
        try:
            mask_db = _spectral.masking_model(
                mag_db, offset, int(self.sample_rate), int(cfg[\"fft\"])
            )
        except Exception:
            return gain_db
        limit = mask_db - mag_db
        # The mask may only *relax* the attenuation, never amplify a bin.
        limit = np.minimum(limit, 0.0)
        if snr_db is not None:
            noise_only = snr_db <= float(cfg[\"noise_threshold\"])
            limit = np.where(noise_only, -np.inf, limit)
        return gain_db + amount * np.maximum(0.0, limit - gain_db)
""",
    ),
    # -- R2: the synthesised floor is a power ---------------------------------
    (
        """        noise_power = np.maximum(db_to_lin(floor_db), 1e-18)
        neighbour_gain = median_filter_axis(gain, 1, 9)
        target = noise_power * neighbour_gain * neighbour_gain
        out_power = np.abs(out_spec) ** 2
""",
        """        noise_level = np.maximum(db_to_lin(floor_db), 1e-18)
        neighbour_gain = median_filter_axis(gain, 1, 9)
        # The target is a power - the level of the removed noise after the
        # gain has been applied.  Mixing a level with a power made the
        # synthesised floor far too loud.
        target = (noise_level * neighbour_gain) ** 2
        out_power = np.abs(out_spec) ** 2
""",
    ),
    # -- R5: the whitening follows the same window ----------------------------
    (
        """            broad = broadband_floor(sm_db[-1:], gap=2, span=8)[0]
""",
        """            broad = broadband_floor(
                sm_db[-1:], gap=max(2, self._broad_gap(cfg) // 2),
                span=max(8, self._broad_span(cfg) // 2)
            )[0]
""",
    ),
]


def build_patched(source: str) -> str:
    """Apply every edit to ``source`` and return the patched text."""
    text = source
    for old, new in EDITS:
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"edit does not apply exactly once ({count}):\n{old[:120]}")
        text = text.replace(old, new)
    return text


def already_applied(source: str) -> bool:
    """True when every proposed change is already in the module.

    The owner of the module may apply the change without the patch; the
    patch then becomes history and this script says so instead of failing.
    """
    return all(new in source for _old, new in EDITS)


def main() -> None:
    """Build the patch, write it out and verify it in a throw-away tree."""
    source = MODULE.read_text(encoding="utf-8")
    try:
        patched = build_patched(source)
    except SystemExit:
        if already_applied(source):
            print(
                "the proposed change is already in %s; %s is the historical "
                "diff of the change" % (MODULE, PATCH_OUT)
            )
            return
        raise

    diff = list(difflib.unified_diff(
        source.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        fromfile="a/aurcore/modules/spectral_de_noise.py",
        tofile="b/aurcore/modules/spectral_de_noise.py",
    ))
    PATCH_OUT.write_text("".join(diff), encoding="utf-8")
    print(f"wrote {PATCH_OUT} ({len(diff)} diff lines)")

    # -- verification tree --------------------------------------------------
    tmp = Path(tempfile.gettempdir()) / "auralis_patch_check"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    for name in ("aurcore", "aurserver", "aurui", "tools", "tests", "corpus", "docs",
                 "qa", "scripts", "plugins"):
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, tmp / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (tmp / "qa" / "quality").mkdir(parents=True, exist_ok=True)
    for name in ("pyproject.toml", "requirements.txt", "README.md", "CONTRACT.md"):
        src = ROOT / name
        if src.is_file():
            shutil.copy2(src, tmp / name)
    shutil.copy2(Path(__file__).resolve().parent / "harness.py",
                 tmp / "qa" / "quality" / "harness.py")
    shutil.copy2(Path(__file__).resolve().parent / "verify_patch.py",
                 tmp / "qa" / "quality" / "verify_patch.py")
    (tmp / "aurcore" / "modules" / "spectral_de_noise.py").write_text(patched, encoding="utf-8")
    print(f"verification tree: {tmp}")

    result = subprocess.run(
        [sys.executable, str(tmp / "qa" / "quality" / "verify_patch.py")],
        capture_output=True, text=True, cwd=str(tmp),
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise SystemExit("the patched module failed the verification run")

    # -- the existing test suite against the patched core --------------------
    print("running the existing test suite against the patched core ...")
    suite = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "--no-header", "-p", "no:cacheprovider"],
        capture_output=True, text=True, cwd=str(tmp),
    )
    tail = [line for line in suite.stdout.splitlines()
            if "passed" in line or "failed" in line or line.startswith("FAILED")]
    print("\n".join(tail[-12:]) or suite.stdout.splitlines()[-1])


if __name__ == "__main__":
    main()
