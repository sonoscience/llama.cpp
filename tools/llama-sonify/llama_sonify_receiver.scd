// llama-sonify SuperCollider receiver
// Receives OSC from llama-sonify and maps CV projections to audio synthesis
//
// Usage:
// 1. Open this file in SuperCollider IDE
// 2. Boot the server: Cmd+B (or run the s.boot line)
// 3. Run the setup block (select and Cmd+Enter)
// 4. Run llama-sonify in terminal
// 5. To stop: Cmd+. (stop all)

// ============================================================
// STEP 1: Boot the server (Cmd+Enter on this line)
// ============================================================
s.boot;

// ============================================================
// STEP 2: Run this entire block (select all, Cmd+Enter)
// ============================================================
(
// Configuration
~oscPort = 9000;
~numLayers = 28;  // Qwen 7B has 28 layers

// Storage for incoming values (per CV, per layer)
~cvData = Dictionary.new;
~cvNames = List.new;
~smoothedValues = Dictionary.new;

// Control buses for smoothed CV values (will be created dynamically)
~cvBuses = Dictionary.new;

// Open OSC port
thisProcess.openUDPPort(~oscPort);
"OSC listening on port %".format(~oscPort).postln;

// -----------------------------------------------------
// OSC Receivers
// -----------------------------------------------------

// Handle /generating messages
OSCdef(\generating, { |msg|
    var state = msg[1];
    if (state == 1) {
        ">>> Generation STARTED".postln;
        // Reset accumulators
        ~cvData.keys.do { |key|
            ~cvData[key] = Array.fill(~numLayers, 0);
        };
    } {
        ">>> Generation STOPPED".postln;
    };
}, '/generating');

// Handle /cv/{name}/{layer} messages
OSCdef(\cvProjection, { |msg, time, addr, port|
    var path = msg[0].asString;
    var value = msg[1];
    var parts = path.split($/);
    var cvName, layer;

    // Parse /cv/{name}/{layer}
    if (parts.size >= 4) {
        cvName = parts[2];
        layer = parts[3].asInteger;

        // Initialize storage if needed
        if (~cvData[cvName].isNil) {
            ~cvData[cvName] = Array.fill(~numLayers, 0);
            ~cvNames.add(cvName);
            ~smoothedValues[cvName] = 0;
            // Create a control bus for this CV
            ~cvBuses[cvName] = Bus.control(s, 1);
            "New CV detected: %".format(cvName).postln;
        };

        // Store the value
        if (layer > 0 && layer <= ~numLayers) {
            ~cvData[cvName][layer - 1] = value;
        };

        // Update smoothed value (exponential moving average across layers)
        ~smoothedValues[cvName] = ~cvData[cvName].mean;

        // Write to control bus
        ~cvBuses[cvName].set(~smoothedValues[cvName]);
    };
}, '/cv');

// Debug: print incoming values periodically
~debugTask = Task({
    loop {
        if (~cvNames.size > 0) {
            "--- CV Values ---".postln;
            ~cvNames.do { |name|
                "  %: % (layers min:% max:%)".format(
                    name.asString.keep(30),
                    ~smoothedValues[name].round(0.001),
                    ~cvData[name].minItem.round(0.01),
                    ~cvData[name].maxItem.round(0.01)
                ).postln;
            };
        };
        2.wait;
    };
});
~debugTask.play;

"Setup complete. Waiting for OSC messages...".postln;
)

// ============================================================
// STEP 3: Run the synth (after llama-sonify sends some data)
// ============================================================
(
// SynthDef: Three-voice drone controlled by CV projections
SynthDef(\llamaDrone, {
    arg out = 0,
        empathyBus = 0,
        optimismBus = 0,
        languageBus = 0,
        masterAmp = 0.3;

    var empathy, optimism, language;
    var sig1, sig2, sig3, mix;
    var freq1, freq2, freq3;

    // Read control values from buses (with lag for smoothing)
    empathy = In.kr(empathyBus).lag(0.5).linlin(-3, 3, 0, 1);
    optimism = In.kr(optimismBus).lag(0.5).linlin(-3, 3, 0, 1);
    language = In.kr(languageBus).lag(0.5).linlin(-3, 3, 0, 1);

    // Voice 1: Empathy controls a warm pad
    freq1 = empathy.linexp(0, 1, 80, 320);
    sig1 = LFTri.ar(freq1) + LFTri.ar(freq1 * 1.5);
    sig1 = LPF.ar(sig1, empathy.linexp(0, 1, 200, 2000));
    sig1 = sig1 * empathy.linlin(0, 1, 0.1, 0.5);

    // Voice 2: Optimism controls brightness/shimmer
    freq2 = optimism.linexp(0, 1, 200, 800);
    sig2 = Saw.ar([freq2, freq2 * 1.01]) * 0.3;
    sig2 = RLPF.ar(sig2, optimism.linexp(0, 1, 400, 4000), 0.3);
    sig2 = sig2 * optimism.linlin(0, 1, 0.05, 0.4);

    // Voice 3: Language controls texture/complexity
    freq3 = language.linexp(0, 1, 60, 240);
    sig3 = Pulse.ar(freq3, language.linlin(0, 1, 0.1, 0.5));
    sig3 = CombL.ar(sig3, 0.2, language.linlin(0, 1, 0.05, 0.15), 2);
    sig3 = LPF.ar(sig3, language.linexp(0, 1, 300, 1500));
    sig3 = sig3 * language.linlin(0, 1, 0.1, 0.3);

    // Mix and output
    mix = (sig1 + sig2 + sig3) * masterAmp;
    mix = LeakDC.ar(mix);
    mix = Limiter.ar(mix, 0.9);

    Out.ar(out, mix);
}).add;

// Wait for SynthDef to be ready
s.sync;

// Start the synth (will use whatever buses exist)
~synth = Synth(\llamaDrone, [
    \empathyBus, ~cvBuses["qwen-2.5:7b-empathy_vs_sociopathy__empathy"] ?? Bus.control(s,1),
    \optimismBus, ~cvBuses["qwen-2.5:7b-optimism_vs_nihilism__optimism"] ?? Bus.control(s,1),
    \languageBus, ~cvBuses["qwen-2.5:7b-language__ornate"] ?? Bus.control(s,1),
    \masterAmp, 0.3
]);

"Synth started. Run llama-sonify to hear it respond.".postln;
)

// ============================================================
// STEP 4: Alternative - Simpler test tone
// ============================================================
(
// Simple test: just map one CV to pitch
SynthDef(\simpleTest, {
    arg out = 0, cvBus = 0;
    var cv = In.kr(cvBus).lag(0.3);
    var freq = cv.linexp(-2, 2, 200, 800);
    var sig = SinOsc.ar(freq) * 0.2;
    Out.ar(out, sig ! 2);
}).add;

s.sync;

// Pick one of the CV buses (if they exist)
~testSynth = Synth(\simpleTest, [
    \cvBus, ~cvBuses.values.asArray[0] ?? Bus.control(s,1)
]);
"Simple test synth started.".postln;
)

// ============================================================
// Cleanup (run when done)
// ============================================================
(
~debugTask.stop;
OSCdef(\generating).free;
OSCdef(\cvProjection).free;
~synth.free;
~cvBuses.do(_.free);
"Cleaned up.".postln;
)
