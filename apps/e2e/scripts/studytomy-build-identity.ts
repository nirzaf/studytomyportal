import { currentForkIdentity } from '../core/studytomyIdentity'

const identity = currentForkIdentity()
console.log('# Set these variables in each local web/API server shell before starting it.')
console.log('export STUDYTOMY_TEST_RUNTIME_IDENTITY=1')
console.log(`export STUDYTOMY_BUILD_SHA=${identity.commitSha}`)
console.log(`export STUDYTOMY_BUILD_TREE_SHA256=${identity.sourceTreeSha256}`)
