"""I want this to be a straightforward and easy to understand
implementation of the signing procedure that can be ripped from this
library and used on its own.

See also:
    https://ripple.com/wiki/User:Singpolyma/Transaction_Signing
"""

import hashlib
from ecdsa import curves, SigningKey
from ecdsa.util import sigencode_der
from serialize import (
    to_bytes, from_bytes, RippleBaseDecoder, serialize_object, fmt_hex)


__all__ = ('sign_transaction', 'signature_for_transaction')


def sign_transaction(transaction, secret):
    """Adds a signature (``TxnSignature``) field to the transaction object.
    """
    sig = signature_for_transaction(transaction, secret)
    transaction['TxnSignature'] = sig
    return transaction


def signature_for_transaction(transaction, secret):
    """Calculate the signature of the transaction.

    Will set the ``SigningPubKey`` as appropriate before signing.

    ``transaction`` is a Python object. The result value is what you
    can insert into as ``TxSignature`` into the transaction structure
    you submit.
    """
    seed = parse_seed(secret)
    key = root_key_from_seed(seed)

    # Apparently the pub key is required to be there.
    transaction['SigningPubKey'] = fmt_hex(ecc_point_to_bytes_compressed(
        key.privkey.public_key.point, pad=True))

    # Convert the transaction to a binary representation
    signing_hash = create_signing_hash(transaction)

    # Create a hex-formatted signature.
    return fmt_hex(ecdsa_sign(key, signing_hash))


def parse_seed(secret):
    """Your Ripple secret is a seed from which the true private key can
    be derived.

    The ``Seed.parse_json()`` method of ripple-lib supports different
    ways of specifying the seed, including a 32-byte hex value. We just
    support the regular base-encoded secret format given to you by the
    client when creating an account.
    """
    assert secret[0] == 's'
    return RippleBaseDecoder.decode(secret)


def root_key_from_seed(seed):
    """This derives your master key the given seed.

    Implemented in ripple-lib as ``Seed.prototype.get_key``, and further
    is described here:
    https://ripple.com/wiki/Account_Family#Root_Key_.28GenerateRootDeterministicKey.29
    """
    seq = 0
    while True:
        private_gen = from_bytes(first_half_of_sha512(
            '{}{}'.format(seed, to_bytes(seq, 4))))
        seq += 1
        if curves.SECP256k1.order >= private_gen:
            break

    public_gen = curves.SECP256k1.generator * private_gen

    # Now that we have the private and public generators, we apparently
    # have to calculate a secret from them that can be used as a ECDSA
    # signing key.
    secret = i = 0
    public_gen_compressed = ecc_point_to_bytes_compressed(public_gen)
    while True:
        secret = from_bytes(first_half_of_sha512(
            "{}{}{}".format(
                public_gen_compressed, to_bytes(0, 4), to_bytes(i, 4))))
        i += 1
        if curves.SECP256k1.order >= secret:
            break
    secret = secret + private_gen % curves.SECP256k1.order

    # The ECDSA signing key object will, given this secret, then expose
    # the actual private and public key we are supposed to work with.
    key = SigningKey.from_secret_exponent(secret, curves.SECP256k1)
    # Attach the generators as supplemental data
    key.private_gen = private_gen
    key.public_gen = public_gen
    return key


def ecdsa_sign(key, bytes):
    """Sign the given data. The key is the secret returned by
    :func:`root_key_from_seed`.

    The data will be a binary coded transaction.
    """
    r, s = key.sign_number(from_bytes(bytes))
    # Encode signature in DER format, as in.
    # As in ``sjcl.ecc.ecdsa.secretKey.prototype.encodeDER``
    der_coded = sigencode_der(r, s, None)
    return der_coded


def get_ripple_from_pubkey(pubkey):
    """Given a public key, determine the Ripple address.
    """
    ripemd160 = hashlib.new('ripemd160')
    ripemd160.update(hashlib.sha256(pubkey).digest())
    print(ripemd160.digest().encode('hex').upper())
    return RippleBaseDecoder.encode(ripemd160.digest())


def get_ripple_from_secret(seed):
    """Another helper. Returns the first ripple address from the secret."""
    key = root_key_from_seed(parse_seed(seed))
    pubkey = ecc_point_to_bytes_compressed(key.privkey.public_key.point, pad=True)
    return get_ripple_from_pubkey(pubkey)


# From ripple-lib:hashprefixes.js
HASH_TX_SIGN = 0x53545800  # 'STX'
HASH_TX_SIGN_TESTNET = 0x73747800 # 'stx'

def create_signing_hash(transaction, testnet=False):
    """This is the actual value to be signed.

    It consists of a prefix and the binary representation of the transaction.
    """
    prefix = HASH_TX_SIGN_TESTNET if testnet else HASH_TX_SIGN
    binary = first_half_of_sha512(
        to_bytes(prefix, 4),
        serialize_object(transaction))
    return binary.encode('hex').upper()


def first_half_of_sha512(*bytes):
    """As per spec, this is the hashing function used."""
    hash = hashlib.sha512()
    for part in bytes:
        hash.update(part)
    return hash.digest()[:256/8]


def ecc_point_to_bytes_compressed(point, pad=False):
    """
    In ripple-lib, implemented as a prototype extension
    ``sjcl.ecc.point.prototype.toBytesCompressed`` in ``sjcl-custom``.

    Also implemented as ``KeyPair.prototype._pub_bits``, though in
    that case it explicitly first pads the point to the bit length of
    the curve prime order value.
    """

    header = '\x02' if point.y() % 2 == 0 else '\x03'
    bytes = to_bytes(
        point.x(),
        curves.SECP256k1.order.bit_length()/8 if pad else None)
    return "{}{}".format(header, bytes)


class Test:

    def test_parse_seed(self):
        # To get the reference value in ripple-lib:
        #    Seed.from_json(...)._value.toString()
        parsed = parse_seed('ssq55ueDob4yV3kPVnNQLHB6icwpC')
        assert from_bytes(parsed) == \
               109259249403722017025835552665225484154

    def test_wiki_test_vector(self):
        # https://ripple.com/wiki/Account_Family#Test_Vectors
        seed = parse_seed('shHM53KPZ87Gwdqarm1bAmPeXg8Tn')
        assert fmt_hex(seed) == '71ED064155FFADFA38782C5E0158CB26'

        key = root_key_from_seed(seed)
        assert fmt_hex(to_bytes(key.private_gen)) == \
               '7CFBA64F771E93E817E15039215430B53F7401C34931D111EAB3510B22DBB0D8'

        assert get_ripple_from_pubkey(
            ecc_point_to_bytes_compressed(key.privkey.public_key.point, pad=True)) == \
            'rhcfR9Cg98qCxHpCcPBmMonbDBXo84wyTn'

    def test_ripple_from_secret(self):
        assert get_ripple_from_secret('shHM53KPZ87Gwdqarm1bAmPeXg8Tn') ==\
               'rhcfR9Cg98qCxHpCcPBmMonbDBXo84wyTn'
