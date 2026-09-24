module mini-helm

go 1.23

replace dario.cat/mergo => github.com/darccio/mergo v1.0.1

replace golang.org/x/crypto => github.com/golang/crypto v0.26.0

replace gopkg.in/yaml.v3 => github.com/go-yaml/yaml/v3 v3.0.1

replace gopkg.in/yaml.v2 => github.com/go-yaml/yaml v2.4.0+incompatible

replace gopkg.in/check.v1 => github.com/go-check/check v0.0.0-20161208181325-20d25e280405

require (
	github.com/Masterminds/sprig/v3 v3.3.0
	github.com/goccy/go-yaml v1.19.2
)

require (
	dario.cat/mergo v1.0.1 // indirect
	github.com/Masterminds/goutils v1.1.1 // indirect
	github.com/Masterminds/semver/v3 v3.3.0 // indirect
	github.com/google/uuid v1.6.0 // indirect
	github.com/huandu/xstrings v1.5.0 // indirect
	github.com/mitchellh/copystructure v1.2.0 // indirect
	github.com/mitchellh/reflectwalk v1.0.2 // indirect
	github.com/shopspring/decimal v1.4.0 // indirect
	github.com/spf13/cast v1.7.0 // indirect
	golang.org/x/crypto v0.26.0 // indirect
)
