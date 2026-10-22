// ============================================================================
//  qa/vst3host/measurements.cpp
//
//  The implementation of the host's measuring instruments.  See
//  measurements.h for what they are for.
// ============================================================================

#include "measurements.h"

#include <cmath>

namespace auralis
{

uint64_t hashBytes (const void* data, size_t size)
{
    const auto* bytes = static_cast<const unsigned char*> (data);

    uint64_t hash = 1469598103934665603ull;

    for (size_t i = 0; i < size; ++i)
    {
        hash ^= static_cast<uint64_t> (bytes[i]);
        hash *= 1099511628211ull;
    }

    return hash;
}

double peakOf (const std::vector<float>& signal)
{
    double peak = 0.0;

    for (float value : signal)
        peak = std::max (peak, std::fabs (static_cast<double> (value)));

    return peak;
}

double rmsOf (const std::vector<float>& signal)
{
    if (signal.empty())
        return 0.0;

    double sum = 0.0;

    for (float value : signal)
    {
        const double v = static_cast<double> (value);
        sum += v * v;
    }

    return std::sqrt (sum / static_cast<double> (signal.size()));
}

double maxDifference (const std::vector<float>& a, const std::vector<float>& b)
{
    const size_t count = std::min (a.size(), b.size());

    double worst = 0.0;

    for (size_t i = 0; i < count; ++i)
    {
        const double difference = std::fabs (static_cast<double> (a[i]) - static_cast<double> (b[i]));
        worst = std::max (worst, difference);
    }

    return worst;
}

int64_t onsetOf (const std::vector<float>& signal, double threshold)
{
    for (size_t i = 0; i < signal.size(); ++i)
        if (std::fabs (static_cast<double> (signal[i])) >= threshold)
            return static_cast<int64_t> (i);

    return -1;
}

int64_t tailAfter (const std::vector<float>& signal, size_t signalLength, double threshold)
{
    if (signalLength > signal.size())
        signalLength = signal.size();

    int64_t last = -1;

    for (size_t i = signalLength; i < signal.size(); ++i)
        if (std::fabs (static_cast<double> (signal[i])) >= threshold)
            last = static_cast<int64_t> (i);

    if (last < 0)
        return 0;

    return last - static_cast<int64_t> (signalLength) + 1;
}

int64_t correlationLag (const std::vector<float>& reference, const std::vector<float>& signal,
                        int64_t maxLag, double* peak)
{
    if (reference.empty() || signal.empty())
    {
        if (peak != nullptr)
            *peak = 0.0;
        return 0;
    }

    if (maxLag > static_cast<int64_t> (signal.size()) - 1)
        maxLag = static_cast<int64_t> (signal.size()) - 1;

    int64_t bestLag = 0;
    double best = -1.0;

    for (int64_t lag = 0; lag <= maxLag; ++lag)
    {
        double sum = 0.0;

        // signal[n + lag] against reference[n]: the lag at which the signal
        // looks most like the reference, shifted.
        const size_t count = std::min (reference.size(), signal.size() - static_cast<size_t> (lag));

        for (size_t n = 0; n < count; ++n)
        {
            sum += static_cast<double> (reference[n])
                   * static_cast<double> (signal[n + static_cast<size_t> (lag)]);
        }

        if (sum > best)
        {
            best = sum;
            bestLag = lag;
        }
    }

    // The peak is normalised, so it is between -1 and 1 and says how much
    // of the signal is explained by the reference at that lag.
    if (peak != nullptr)
    {
        double referenceEnergy = 0.0;
        double signalEnergy = 0.0;

        for (size_t n = 0; n < reference.size(); ++n)
            referenceEnergy += static_cast<double> (reference[n]) * static_cast<double> (reference[n]);

        const size_t count = std::min (reference.size(),
                                       signal.size() - static_cast<size_t> (bestLag));
        for (size_t n = 0; n < count; ++n)
            signalEnergy += static_cast<double> (signal[n + static_cast<size_t> (bestLag)])
                           * static_cast<double> (signal[n + static_cast<size_t> (bestLag)]);

        const double denominator = std::sqrt (referenceEnergy * signalEnergy);
        *peak = denominator > 0.0 ? best / denominator : 0.0;
    }

    return bestLag;
}

std::vector<float> makeTone (double hz, double amplitude, size_t count, double sampleRate)
{
    std::vector<float> signal (count);

    constexpr double kTwoPi = 6.283185307179586476925286766559;

    for (size_t n = 0; n < count; ++n)
    {
        const double phase = kTwoPi * hz * static_cast<double> (n) / sampleRate;
        signal[n] = static_cast<float> (amplitude * std::sin (phase));
    }

    return signal;
}

std::vector<float> makeSweep (double f0, double f1, double amplitude, size_t count,
                               double sampleRate)
{
    std::vector<float> signal (count);

    if (count == 0)
        return signal;

    constexpr double kTwoPi = 6.283185307179586476925286766559;

    const double total = static_cast<double> (count) / sampleRate;
    const double rate = (f1 - f0) / total;
    double phase = 0.0;

    for (size_t n = 0; n < count; ++n)
    {
        const double time = static_cast<double> (n) / sampleRate;
        phase += kTwoPi * (f0 + rate * time) / sampleRate;
        signal[n] = static_cast<float> (amplitude * std::sin (phase));
    }

    return signal;
}

std::vector<float> makeImpulse (double amplitude, size_t count)
{
    std::vector<float> signal (count, 0.f);

    if (count > 0)
        signal[0] = static_cast<float> (amplitude);

    return signal;
}

std::vector<float> makeMixed (double hz, double amplitude, size_t count, double sampleRate)
{
    std::vector<float> signal (count, 0.f);

    constexpr double kTwoPi = 6.283185307179586476925286766559;

    // A deterministic noise source: a linear congruential generator, so
    // that two runs of the exercise feed the very same signal in.
    uint32_t seed = 22222u;

    const size_t clickEvery = 1024;

    for (size_t n = 0; n < count; ++n)
    {
        const double phase = kTwoPi * hz * static_cast<double> (n) / sampleRate;

        double value = 0.8 * std::sin (phase);

        if (n % clickEvery == 0)
            value += 0.6;

        seed = seed * 1664525u + 1013904223u;
        const double noise = (static_cast<double> (seed >> 8) / 8388608.0) - 1.0;
        value += 0.02 * noise;

        signal[n] = static_cast<float> (amplitude * value);
    }

    return signal;
}

int32_t countSpeakers (uint64_t arrangement)
{
    int32_t count = 0;

    while (arrangement != 0)
    {
        count += static_cast<int32_t> (arrangement & 1ull);
        arrangement >>= 1;
    }

    return count;
}

} // namespace auralis
