class Alpha:
    def read(self):
        return 'alpha'

class Beta:
    def read(self):
        return 'beta'

def known():
    receiver = Alpha()
    return receiver.read()

def unknown(receiver):
    return receiver.read()

def reassigned():
    receiver = Alpha()
    receiver = Beta()
    return receiver.read()

def explicit(receiver):
    return Alpha.read(receiver)

def shadowed(read):
    return read()

def same_line():
    receiver = Alpha(); receiver = Beta(); return receiver.read()

class Holder:
    def read(self):
        return 'holder'
    def outer(self):
        def nested(self):
            return self.read()
        return nested(self)
    @staticmethod
    def arbitrary(self):
        return self.read()
