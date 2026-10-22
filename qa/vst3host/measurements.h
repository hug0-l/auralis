// ============================================================================
//  qa/vst3host/measurements.h
//
//  The measuring instruments of the Auralis VST3 host.
//
//  A host that only asks a plug-in questions is not much use: it has to
//  measure what the plug-in actually does.  These helpers are the small
//  instruments the host uses:
//
//    * a hash, so two states can be compared without printing them,
//    * the peak, the root mean square and the difference of two signals,
//    * the onset of a signal, which is where the plug-in's output starts,
//    * the tail after a signal stops, which is how long the plug-in rings,
//    * the lag of a cross correlation, which is the delay the plug-in adds,
//    * the test signals themselves: a tone, a sweep and an impulse.
//
//  Nothing here knows anything about the plug-in under test.  Every function
//  takes plain numbers and plain vectors, so the host stays independent of
//  the module it exercises.
// ============================================================================

#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace auralis
{

/** The FNV-1a hash of a byte range.  Two states with the same hash are the
    same state, for every practical purpose. */
uint64_t hashBytes (const void* data, size_t size);

/** The largest magnitude in the signal. */
double peakOf (const std::vector<float>& signal);

/** The root mean square of the signal. */
double rmsOf (const std::vector<float>& signal);

/** The largest difference between two signals; the signals may differ in
    length, and the shorter one decides how far the comparison goes. */
double maxDifference (const std::vector<float>& a, const std::vector<float>& b);

/** The index of the first sample whose magnitude reaches the threshold, or
    -1 when the signal never reaches it. */
int64_t onsetOf (const std::vector<float>& signal, double threshold);

/** The number of samples after the signal proper (the first `signalLength`
    samples) that still reach the threshold.  This is how long the plug-in
    rings after its input stopped. */
int64_t tailAfter (const std::vector<float>& signal, size_t signalLength, double threshold);

/** The lag at which the cross correlation of a reference and a signal is
    largest.  A plug-in that delays its input by L samples gives L here.
    The peak of the correlation is written to `peak` when it is wanted: it
    says how well the signal matches the reference at all. */
int64_t correlationLag (const std::vector<float>& reference,
                        const std::vector<float>& signal,
                        int64_t maxLag,
                        double* peak = nullptr);

/** A sine wave of the given frequency and amplitude. */
std::vector<float> makeTone (double hz, double amplitude, size_t count, double sampleRate);

/** A linear sweep from `f0` to `f1`.  A sweep correlates sharply against
    itself, so the lag of a sweep says where the delay is without the
    ambiguity a pure tone would have. */
std::vector<float> makeSweep (double f0, double f1, double amplitude, size_t count,
                               double sampleRate);

/** A signal that has something in it for every kind of module: a tone for
    the modules that work on pitch, a click every so often for the modules
    that work on transients, and a little noise so that the modules that
    work on noise have something to work on.  The noise is deterministic,
    so two runs of the same signal are the same signal. */
std::vector<float> makeMixed (double hz, double amplitude, size_t count, double sampleRate);

/** An impulse in the first sample. */
std::vector<float> makeImpulse (double amplitude, size_t count);

/** The number of set bits in a speaker arrangement, which is its channel
    count. */
int32_t countSpeakers (uint64_t arrangement);

} // namespace auralis
