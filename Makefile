ifndef JUJU_REPOSITORY
	$(error JUJU_REPOSITORY is undefined)
endif

all: $(JUJU_REPOSITORY)/trusty/prometheus

$(JUJU_REPOSITORY)/trusty/prometheus:
	LAYER_PATH=$(shell pwd)/layers INTERFACE_PATH=$(shell pwd)/interfaces charm build -s trusty

unittest:
	tox

unittest2:
	tox -e py2

unittest3:
	tox -e py3

clean:
	$(RM) -r $(JUJU_REPOSITORY)/trusty/prometheus

.PHONY: all unittest unittest2 unitest3 clean
