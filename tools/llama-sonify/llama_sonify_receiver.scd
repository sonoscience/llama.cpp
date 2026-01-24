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
// NOTE: Use nil to match ALL OSC messages, then filter by path prefix
// (OSCdef with '/cv' only matches exactly '/cv', not '/cv/name/layer')
OSCdef(\cvProjection, { |msg, time, addr, port|
    var path = msg[0].asString;
    var value = msg[1];
    var parts, cvName, layer;

    // Debug: print first few raw messages
    if (~oscDebugCount.isNil) { ~oscDebugCount = 0 };
    if (~oscDebugCount < 20) {
        "RAW OSC: path=% value=%".format(path, value).postln;
        ~oscDebugCount = ~oscDebugCount + 1;
    };

    // Only process messages starting with /cv/
    if (path.beginsWith("/cv/")) {
        parts = path.split($/);

        // Parse /cv/{name}/{layer}
        if (parts.size >= 4) {
            cvName = parts[2];
            layer = parts[3].asInteger;

            // Initialize storage if needed
            if (~cvData[cvName].isNil) {
                ~cvData[cvName] = Array.fill(~numLayers, 0);
                ~cvNames.add(cvName);
                ~smoothedValues[cvName] = 0;
                "New CV detected: %".format(cvName).postln;
            };

            // Store the value
            if (layer > 0 && layer <= ~numLayers) {
                ~cvData[cvName][layer - 1] = value;
            };

            // Update smoothed value (mean across layers)
            ~smoothedValues[cvName] = ~cvData[cvName].mean;

            // Write to the appropriate pre-created bus based on CV name pattern
            // Match by the semantic part of the name (empathy, optimism, ornate)
            if (cvName.contains("empathy")) {
                ~empathyBus !? { ~empathyBus.set(~smoothedValues[cvName]) };
            };
            if (cvName.contains("optimism")) {
                ~optimismBus !? { ~optimismBus.set(~smoothedValues[cvName]) };
            };
            if (cvName.contains("ornate") || cvName.contains("language")) {
                ~languageBus !? { ~languageBus.set(~smoothedValues[cvName]) };
            };
        };
    };
}, nil);  // nil matches ALL incoming OSC messages

// Debug: print incoming values periodically
~debugTask = Task({
    loop {
        "--- Status ---".postln;
        // Show bus values if buses exist
        if (~empathyBus.notNil) {
            ~empathyBus.get({ |v| "  empathyBus[%]: %".format(~empathyBus.index, v.round(0.001)).postln });
        };
        if (~optimismBus.notNil) {
            ~optimismBus.get({ |v| "  optimismBus[%]: %".format(~optimismBus.index, v.round(0.001)).postln });
        };
        if (~languageBus.notNil) {
            ~languageBus.get({ |v| "  languageBus[%]: %".format(~languageBus.index, v.round(0.001)).postln });
        };
        // Show CV names detected
        if (~cvNames.size > 0) {
            "  CVs detected: %".format(~cvNames.size).postln;
        };
        2.wait;
    };
});
~debugTask.play;

"Setup complete. Waiting for OSC messages...".postln;
)

// ============================================================
// STEP 3: Run the synth
// ============================================================
(
// Pre-create control buses for the expected CVs
// These will be written to by the OSC receiver when messages arrive
~empathyBus = Bus.control(s, 1);
~optimismBus = Bus.control(s, 1);
~languageBus = Bus.control(s, 1);

// Set initial values (neutral)
~empathyBus.set(0);
~optimismBus.set(0);
~languageBus.set(0);

"Created control buses: empathy=%, optimism=%, language=%".format(
    ~empathyBus.index, ~optimismBus.index, ~languageBus.index
).postln;

// Use fork to allow s.sync inside
fork {
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

    // Start the synth with our pre-created buses
    ~synth = Synth(\llamaDrone, [
        \empathyBus, ~empathyBus.index,
        \optimismBus, ~optimismBus.index,
        \languageBus, ~languageBus.index,
        \masterAmp, 0.3
    ]);

    "Synth started on buses: empathy=%, optimism=%, language=%".format(
        ~empathyBus.index, ~optimismBus.index, ~languageBus.index
    ).postln;
};
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
~synth !? { ~synth.free };
~empathyBus !? { ~empathyBus.free };
~optimismBus !? { ~optimismBus.free };
~languageBus !? { ~languageBus.free };
"Cleaned up.".postln;
)
