#!/usr/bin/env python3

"""Generate programs/psa/psa_constant_names_generated.c
which is included by programs/psa/psa_constant_names.c.
The code generated by this module is only meant to be used in the context
of that program.
"""

import os
import re

OUTPUT_TEMPLATE = '''\
/* Automatically generated by generate_psa_constant.py. DO NOT EDIT. */

static const char *psa_strerror(psa_status_t status)
{
    switch (status) {
    %(status_cases)s
    default: return NULL;
    }
}

static const char *psa_ecc_curve_name(psa_ecc_curve_t curve)
{
    switch (curve) {
    %(ecc_curve_cases)s
    default: return NULL;
    }
}

static const char *psa_dh_group_name(psa_dh_group_t group)
{
    switch (group) {
    %(dh_group_cases)s
    default: return NULL;
    }
}

static const char *psa_hash_algorithm_name(psa_algorithm_t hash_alg)
{
    switch (hash_alg) {
    %(hash_algorithm_cases)s
    default: return NULL;
    }
}

static const char *psa_ka_algorithm_name(psa_algorithm_t ka_alg)
{
    switch (ka_alg) {
    %(ka_algorithm_cases)s
    default: return NULL;
    }
}

static int psa_snprint_key_type(char *buffer, size_t buffer_size,
                                psa_key_type_t type)
{
    size_t required_size = 0;
    switch (type) {
    %(key_type_cases)s
    default:
        %(key_type_code)s{
            return snprintf(buffer, buffer_size,
                            "0x%%08lx", (unsigned long) type);
        }
        break;
    }
    buffer[0] = 0;
    return (int) required_size;
}

#define NO_LENGTH_MODIFIER 0xfffffffflu
static int psa_snprint_algorithm(char *buffer, size_t buffer_size,
                                 psa_algorithm_t alg)
{
    size_t required_size = 0;
    psa_algorithm_t core_alg = alg;
    unsigned long length_modifier = NO_LENGTH_MODIFIER;
    if (PSA_ALG_IS_MAC(alg)) {
        core_alg = PSA_ALG_TRUNCATED_MAC(alg, 0);
        if (core_alg != alg) {
            append(&buffer, buffer_size, &required_size,
                   "PSA_ALG_TRUNCATED_MAC(", 22);
            length_modifier = PSA_MAC_TRUNCATED_LENGTH(alg);
        }
    } else if (PSA_ALG_IS_AEAD(alg)) {
        core_alg = PSA_ALG_AEAD_WITH_DEFAULT_TAG_LENGTH(alg);
        if (core_alg == 0) {
            /* For unknown AEAD algorithms, there is no "default tag length". */
            core_alg = alg;
        } else if (core_alg != alg) {
            append(&buffer, buffer_size, &required_size,
                   "PSA_ALG_AEAD_WITH_TAG_LENGTH(", 29);
            length_modifier = PSA_AEAD_TAG_LENGTH(alg);
        }
    } else if (PSA_ALG_IS_KEY_AGREEMENT(alg) &&
               !PSA_ALG_IS_RAW_KEY_AGREEMENT(alg)) {
        core_alg = PSA_ALG_KEY_AGREEMENT_GET_KDF(alg);
        append(&buffer, buffer_size, &required_size,
               "PSA_ALG_KEY_AGREEMENT(", 22);
        append_with_alg(&buffer, buffer_size, &required_size,
                        psa_ka_algorithm_name,
                        PSA_ALG_KEY_AGREEMENT_GET_BASE(alg));
        append(&buffer, buffer_size, &required_size, ", ", 2);
    }
    switch (core_alg) {
    %(algorithm_cases)s
    default:
        %(algorithm_code)s{
            append_integer(&buffer, buffer_size, &required_size,
                           "0x%%08lx", (unsigned long) core_alg);
        }
        break;
    }
    if (core_alg != alg) {
        if (length_modifier != NO_LENGTH_MODIFIER) {
            append(&buffer, buffer_size, &required_size, ", ", 2);
            append_integer(&buffer, buffer_size, &required_size,
                           "%%lu", length_modifier);
        }
        append(&buffer, buffer_size, &required_size, ")", 1);
    }
    buffer[0] = 0;
    return (int) required_size;
}

static int psa_snprint_key_usage(char *buffer, size_t buffer_size,
                                 psa_key_usage_t usage)
{
    size_t required_size = 0;
    if (usage == 0) {
        if (buffer_size > 1) {
            buffer[0] = '0';
            buffer[1] = 0;
        } else if (buffer_size == 1) {
            buffer[0] = 0;
        }
        return 1;
    }
%(key_usage_code)s
    if (usage != 0) {
        if (required_size != 0) {
            append(&buffer, buffer_size, &required_size, " | ", 3);
        }
        append_integer(&buffer, buffer_size, &required_size,
                       "0x%%08lx", (unsigned long) usage);
    } else {
        buffer[0] = 0;
    }
    return (int) required_size;
}

/* End of automatically generated file. */
'''

KEY_TYPE_FROM_CURVE_TEMPLATE = '''if (%(tester)s(type)) {
            append_with_curve(&buffer, buffer_size, &required_size,
                              "%(builder)s", %(builder_length)s,
                              PSA_KEY_TYPE_GET_CURVE(type));
        } else '''

KEY_TYPE_FROM_GROUP_TEMPLATE = '''if (%(tester)s(type)) {
            append_with_group(&buffer, buffer_size, &required_size,
                              "%(builder)s", %(builder_length)s,
                              PSA_KEY_TYPE_GET_GROUP(type));
        } else '''

ALGORITHM_FROM_HASH_TEMPLATE = '''if (%(tester)s(core_alg)) {
            append(&buffer, buffer_size, &required_size,
                   "%(builder)s(", %(builder_length)s + 1);
            append_with_alg(&buffer, buffer_size, &required_size,
                            psa_hash_algorithm_name,
                            PSA_ALG_GET_HASH(core_alg));
            append(&buffer, buffer_size, &required_size, ")", 1);
        } else '''

BIT_TEST_TEMPLATE = '''\
    if (%(var)s & %(flag)s) {
        if (required_size != 0) {
            append(&buffer, buffer_size, &required_size, " | ", 3);
        }
        append(&buffer, buffer_size, &required_size, "%(flag)s", %(length)d);
        %(var)s ^= %(flag)s;
    }\
'''

class MacroCollector:
    """Collect PSA crypto macro definitions from C header files.

    1. Call `read_file` on the input header file(s).
    2. Call `write_file` to write ``psa_constant_names_generated.c``.
    """

    def __init__(self):
        self.statuses = set()
        self.key_types = set()
        self.key_types_from_curve = {}
        self.key_types_from_group = {}
        self.ecc_curves = set()
        self.dh_groups = set()
        self.algorithms = set()
        self.hash_algorithms = set()
        self.ka_algorithms = set()
        self.algorithms_from_hash = {}
        self.key_usages = set()

    # "#define" followed by a macro name with either no parameters
    # or a single parameter and a non-empty expansion.
    # Grab the macro name in group 1, the parameter name if any in group 2
    # and the expansion in group 3.
    _define_directive_re = re.compile(r'\s*#\s*define\s+(\w+)' +
                                      r'(?:\s+|\((\w+)\)\s*)' +
                                      r'(.+)')

    def read_line(self, line):
        """Parse a C header line and record the PSA identifier it defines if any.
        This function analyzes lines that start with "#define PSA_"
        (up to non-significant whitespace) and skips all non-matching lines.
        """
        # pylint: disable=too-many-branches
        m = re.match(self._define_directive_re, line)
        if not m:
            return
        name, parameter, expansion = m.groups()
        expansion = re.sub(r'/\*.*?\*/|//.*', r' ', expansion)
        if name.endswith('_FLAG') or name.endswith('MASK'):
            # Macro only to build actual values
            return
        elif (name.startswith('PSA_ERROR_') or name == 'PSA_SUCCESS') \
           and not parameter:
            if name in ['PSA_ERROR_UNKNOWN_ERROR',
                        'PSA_ERROR_OCCUPIED_SLOT',
                        'PSA_ERROR_EMPTY_SLOT',
                        'PSA_ERROR_INSUFFICIENT_CAPACITY',
                       ]:
                # Ad hoc skipping of deprecated error codes, which share
                # numerical values with non-deprecated error codes
                return

            self.statuses.add(name)
        elif name.startswith('PSA_KEY_TYPE_') and not parameter:
            self.key_types.add(name)
        elif name.startswith('PSA_KEY_TYPE_') and parameter == 'curve':
            self.key_types_from_curve[name] = name[:13] + 'IS_' + name[13:]
        elif name.startswith('PSA_KEY_TYPE_') and parameter == 'group':
            self.key_types_from_group[name] = name[:13] + 'IS_' + name[13:]
        elif name.startswith('PSA_ECC_CURVE_') and not parameter:
            self.ecc_curves.add(name)
        elif name.startswith('PSA_DH_GROUP_') and not parameter:
            self.dh_groups.add(name)
        elif name.startswith('PSA_ALG_') and not parameter:
            if name in ['PSA_ALG_ECDSA_BASE',
                        'PSA_ALG_RSA_PKCS1V15_SIGN_BASE']:
                # Ad hoc skipping of duplicate names for some numerical values
                return
            self.algorithms.add(name)
            # Ad hoc detection of hash algorithms
            if re.search(r'0x010000[0-9A-Fa-f]{2}', expansion):
                self.hash_algorithms.add(name)
            # Ad hoc detection of key agreement algorithms
            if re.search(r'0x30[0-9A-Fa-f]{2}0000', expansion):
                self.ka_algorithms.add(name)
        elif name.startswith('PSA_ALG_') and parameter == 'hash_alg':
            if name in ['PSA_ALG_DSA', 'PSA_ALG_ECDSA']:
                # A naming irregularity
                tester = name[:8] + 'IS_RANDOMIZED_' + name[8:]
            else:
                tester = name[:8] + 'IS_' + name[8:]
            self.algorithms_from_hash[name] = tester
        elif name.startswith('PSA_KEY_USAGE_') and not parameter:
            self.key_usages.add(name)
        else:
            # Other macro without parameter
            return

    def read_file(self, header_file):
        for line in header_file:
            while line.endswith('\\\n'):
                cont = next(header_file)
                line = line[:-2] + cont
            self.read_line(line)

    @staticmethod
    def _make_return_case(name):
        return 'case %(name)s: return "%(name)s";' % {'name': name}

    @staticmethod
    def _make_append_case(name):
        template = ('case %(name)s: '
                    'append(&buffer, buffer_size, &required_size, "%(name)s", %(length)d); '
                    'break;')
        return template % {'name': name, 'length': len(name)}

    @staticmethod
    def _make_bit_test(var, flag):
        return BIT_TEST_TEMPLATE % {'var': var,
                                    'flag': flag,
                                    'length': len(flag)}

    def _make_status_cases(self):
        return '\n    '.join(map(self._make_return_case,
                                 sorted(self.statuses)))

    def _make_ecc_curve_cases(self):
        return '\n    '.join(map(self._make_return_case,
                                 sorted(self.ecc_curves)))

    def _make_dh_group_cases(self):
        return '\n    '.join(map(self._make_return_case,
                                 sorted(self.dh_groups)))

    def _make_key_type_cases(self):
        return '\n    '.join(map(self._make_append_case,
                                 sorted(self.key_types)))

    @staticmethod
    def _make_key_type_from_curve_code(builder, tester):
        return KEY_TYPE_FROM_CURVE_TEMPLATE % {'builder': builder,
                                               'builder_length': len(builder),
                                               'tester': tester}

    @staticmethod
    def _make_key_type_from_group_code(builder, tester):
        return KEY_TYPE_FROM_GROUP_TEMPLATE % {'builder': builder,
                                               'builder_length': len(builder),
                                               'tester': tester}

    def _make_ecc_key_type_code(self):
        d = self.key_types_from_curve
        make = self._make_key_type_from_curve_code
        return ''.join([make(k, d[k]) for k in sorted(d.keys())])

    def _make_dh_key_type_code(self):
        d = self.key_types_from_group
        make = self._make_key_type_from_group_code
        return ''.join([make(k, d[k]) for k in sorted(d.keys())])

    def _make_hash_algorithm_cases(self):
        return '\n    '.join(map(self._make_return_case,
                                 sorted(self.hash_algorithms)))

    def _make_ka_algorithm_cases(self):
        return '\n    '.join(map(self._make_return_case,
                                 sorted(self.ka_algorithms)))

    def _make_algorithm_cases(self):
        return '\n    '.join(map(self._make_append_case,
                                 sorted(self.algorithms)))

    @staticmethod
    def _make_algorithm_from_hash_code(builder, tester):
        return ALGORITHM_FROM_HASH_TEMPLATE % {'builder': builder,
                                               'builder_length': len(builder),
                                               'tester': tester}

    def _make_algorithm_code(self):
        d = self.algorithms_from_hash
        make = self._make_algorithm_from_hash_code
        return ''.join([make(k, d[k]) for k in sorted(d.keys())])

    def _make_key_usage_code(self):
        return '\n'.join([self._make_bit_test('usage', bit)
                          for bit in sorted(self.key_usages)])

    def write_file(self, output_file):
        """Generate the pretty-printer function code from the gathered
        constant definitions.
        """
        data = {}
        data['status_cases'] = self._make_status_cases()
        data['ecc_curve_cases'] = self._make_ecc_curve_cases()
        data['dh_group_cases'] = self._make_dh_group_cases()
        data['key_type_cases'] = self._make_key_type_cases()
        data['key_type_code'] = (self._make_ecc_key_type_code() +
                                 self._make_dh_key_type_code())
        data['hash_algorithm_cases'] = self._make_hash_algorithm_cases()
        data['ka_algorithm_cases'] = self._make_ka_algorithm_cases()
        data['algorithm_cases'] = self._make_algorithm_cases()
        data['algorithm_code'] = self._make_algorithm_code()
        data['key_usage_code'] = self._make_key_usage_code()
        output_file.write(OUTPUT_TEMPLATE % data)

def generate_psa_constants(header_file_names, output_file_name):
    collector = MacroCollector()
    for header_file_name in header_file_names:
        with open(header_file_name) as header_file:
            collector.read_file(header_file)
    temp_file_name = output_file_name + '.tmp'
    with open(temp_file_name, 'w') as output_file:
        collector.write_file(output_file)
    os.rename(temp_file_name, output_file_name)

if __name__ == '__main__':
    if not os.path.isdir('programs') and os.path.isdir('../programs'):
        os.chdir('..')
    generate_psa_constants(['include/psa/crypto_values.h',
                            'include/psa/crypto_extra.h'],
                           'programs/psa/psa_constant_names_generated.c')
