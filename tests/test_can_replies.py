"""Compile and exercise the actual telemetry lambdas without ESP hardware.

Run with: python3 tests/test_can_replies.py (requires a C++ compiler).
"""
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap

config = (Path(__file__).resolve().parents[1] / 'sensor_node.conf').read_text()

def handler(marker):
    section = config.split(marker, 1)[1].split('        - lambda: |-\n', 1)[1]
    lines = []
    for line in section.splitlines():
        if line and not line.startswith('            '):
            break
        lines.append(line)
    return textwrap.dedent('\n'.join(lines))

water = handler('#Storage ACTUAL / hot water temperature:')
counters = handler('#Paired daily energy and compressor replies')
entities = set(re.findall(r'id\((\w+)\)\.publish_state', water + counters))
variables = set(re.findall(r'id\((\w+)\)', water + counters)) - entities - {'internalResponse_id'}
declarations = '\n'.join('Sensor ' + name + ';' for name in sorted(entities))
declarations += '\n' + '\n'.join(
    ('bool ' if name.endswith(('_flag', '_received')) else 'float ') + name + ' = 0;'
    for name in sorted(variables)
)
source = r'''
#include <cassert>
#include <cmath>
#include <cstdint>
#include <vector>
#define id(name) name
struct Sensor {
  float state = NAN;
  int publications = 0;
  void publish_state(float value) { state = value; ++publications; }
};
int internalResponse_id[2] = {0xd2, 0};
''' + declarations + '\nvoid water(std::vector<uint8_t> x) {\n' + water + '\n}\n'
source += 'void counters(std::vector<uint8_t> x) {\n' + counters + '\n}\n'
source += r'''
void reply(uint16_t index, uint16_t raw) {
  counters({0xd2, 0, 0xfa, uint8_t(index >> 8), uint8_t(index),
            uint8_t(raw >> 8), uint8_t(raw)});
}
int main() {
  water({0xd2, 0, 0x0e, 1, 202, 0, 0}); // 45.8 C short reply
  assert(fabs(temperature_water.state - 45.8f) < 0.001f);
  assert(t_ww_ist.state == temperature_water.state);
  water({0xd2, 0, 0xfa, 0, 0x0e, 1, 244}); // 50 C extended reply
  assert(temperature_water.state == 50);
  water({0xd2, 0, 0x0e, 0x80, 0, 0, 0});
  water({0xd2, 0, 0x0e, 0xff, 0xff, 0, 0});
  water({0xd2});
  water({0xe2, 0, 0x0e, 0, 0, 0, 0});
  water({0xd2, 0, 0x13, 0, 0, 0, 0});
  assert(temperature_water.publications == 2);
  struct Pair { uint16_t index; Sensor *sensor; };
  for (auto pair : {Pair{0x091a, &daily_electric_energy_water},
                    Pair{0x091e, &daily_electric_energy_heating},
                    Pair{0x092a, &daily_heating_energy_water},
                    Pair{0x092e, &daily_heating_energy}}) {
    reply(pair.index, 375);
    assert(pair.sensor->publications == 0);
    reply(pair.index + 1, 12);
    assert(pair.sensor->state == 12.375f);
    // New pair, reversed order, including zero kWh and zero Wh.
    reply(pair.index + 1, 0);
    assert(pair.sensor->publications == 1);
    reply(pair.index, 0);
    assert(pair.sensor->state == 0);
    reply(pair.index, 0xffff);
    reply(pair.index + 1, 0x8000);
    assert(pair.sensor->publications == 2);
  }
  reply(0x071d, 0);
  assert(VD_starts.publications == 0);
  reply(0x071c, 12);
  assert(VD_starts.state == 12000);
  reply(0x071c, 0);
  reply(0x071d, 7);
  assert(VD_starts.state == 7);
  reply(0x071c, 0);
  reply(0x071d, 0);
  assert(VD_starts.state == 0);
  counters({0xd2});
  counters({0xe2, 0, 0xfa, 7, 0x1c, 0, 1});
  counters({0xd2, 0, 0x07, 7, 0x1c, 0, 1});
  reply(0x071c, 0xffff);
  reply(0x071d, 0x8000);
  assert(VD_starts.publications == 3);
}
'''
with tempfile.TemporaryDirectory() as temp:
    cpp = Path(temp) / 'test.cpp'
    exe = Path(temp) / 'test'
    cpp.write_text(source)
    subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', str(cpp), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('CAN reply regression checks passed.')
