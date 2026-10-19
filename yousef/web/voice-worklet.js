// Collects microphone samples into 20 ms frames and hands them to the page.
class YousefCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.size = Math.round(sampleRate * 0.02);
    this.buf = new Float32Array(this.size);
    this.fill = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      let i = 0;
      while (i < channel.length) {
        const take = Math.min(channel.length - i, this.size - this.fill);
        this.buf.set(channel.subarray(i, i + take), this.fill);
        this.fill += take;
        i += take;
        if (this.fill === this.size) {
          this.port.postMessage(this.buf.slice(0));
          this.fill = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("yousef-capture", YousefCapture);
